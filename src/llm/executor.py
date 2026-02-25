"""
tg_keto.llm.executor — Run LLM via CLI subprocess with concurrency control.

Architecture:
  1 LLM request = 1 subprocess: gemini -p "prompt_text"
  The subprocess forks, connects to Gemini API (via its own auth), returns text to stdout, exits.

  Concurrency is controlled by asyncio.Semaphore(MAX_LLM_CONCURRENCY).
  When K=1, only one LLM call runs at a time (sequential).
  Other calls wait in the semaphore queue (measured as t_wait_llm).

Under the hood (Linux/macOS):
  asyncio.create_subprocess_exec → fork() + execvp("gemini", ["-p", prompt])
  Parent process (our worker) awaits stdout via pipe (asyncio read).
  Child process (gemini CLI):
    1. Reads prompt from argv
    2. Opens HTTPS to Gemini API (TLS socket)
    3. Streams response tokens
    4. Writes final text to stdout fd=1
    5. exit(0)
  Parent collects stdout bytes, decodes UTF-8, returns string.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

# Semaphore: limits concurrent LLM subprocesses
_semaphore = asyncio.Semaphore(settings.max_llm_concurrency)


async def call_llm(prompt: str) -> str:
    """
    Execute LLM CLI as subprocess with concurrency limiter.

    Returns the raw stdout text from the CLI.
    Raises TimeoutError if CLI doesn't respond within LLM_TIMEOUT_SECONDS.
    Raises RuntimeError on non-zero exit code.
    """
    t_wait_start = time.monotonic()

    async with _semaphore:
        t_wait_end = time.monotonic()
        t_wait_llm = t_wait_end - t_wait_start

        if t_wait_llm > 0.1:
            logger.info("llm_semaphore_waited", wait_seconds=round(t_wait_llm, 2))

        t_exec_start = time.monotonic()

        try:
            # Build command: gemini -p "prompt_text"
            cmd = settings.llm_cli_command
            flags = settings.llm_cli_flags.split() if settings.llm_cli_flags else []

            proc = await asyncio.create_subprocess_exec(
                cmd, *flags, prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.llm_timeout_seconds,
            )

            t_exec_end = time.monotonic()
            t_exec_llm = t_exec_end - t_exec_start

            if proc.returncode != 0:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                logger.error(
                    "llm_cli_error",
                    returncode=proc.returncode,
                    stderr=stderr_text[:500],
                    exec_seconds=round(t_exec_llm, 2),
                )
                raise RuntimeError(f"LLM CLI exited with code {proc.returncode}: {stderr_text[:200]}")

            result = stdout_bytes.decode("utf-8", errors="replace").strip()

            logger.info(
                "llm_cli_success",
                exec_seconds=round(t_exec_llm, 2),
                wait_seconds=round(t_wait_llm, 2),
                output_chars=len(result),
            )

            return result

        except asyncio.TimeoutError:
            t_exec_end = time.monotonic()
            logger.error(
                "llm_cli_timeout",
                timeout=settings.llm_timeout_seconds,
                exec_seconds=round(t_exec_end - t_exec_start, 2),
            )
            # Kill the hung process
            try:
                proc.kill()
            except Exception:
                pass
            raise TimeoutError(f"LLM CLI timed out after {settings.llm_timeout_seconds}s")

        except FileNotFoundError:
            logger.error("llm_cli_not_found", command=settings.llm_cli_command)
            raise RuntimeError(
                f"LLM CLI '{settings.llm_cli_command}' not found. "
                f"Is it installed and in PATH?"
            )
