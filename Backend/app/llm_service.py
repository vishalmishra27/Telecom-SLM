import json
import logging
from pathlib import Path
from typing import Optional

from anthropic import Anthropic
from openai import OpenAI

from .config import Settings

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, settings: Settings, project_root: Path):
        self.settings = settings
        self.project_root = project_root

        # Initialize Claude client
        self.claude = None
        if settings.claude_configured:
            self.claude = Anthropic(api_key=settings.claude_api_key)

        # Initialize OpenAI client
        self.openai = None
        if settings.openai_configured:
            self.openai = OpenAI(api_key=settings.openai_api_key)

        # Load context files
        self._schema_content = self._load_schema()
        self._incident_rca_content = self._load_incident_rca()
        self._raw_csv_context = self._load_raw_csvs()

    def _load_schema(self) -> str:
        """Load schema.json and format as text for inclusion in prompts."""
        try:
            schema_path = self.project_root / "schema.json"
            if schema_path.exists():
                with open(schema_path, "r") as f:
                    schema = json.load(f)
                return json.dumps(schema, indent=2)
        except Exception as e:
            logger.warning(f"Failed to load schema.json: {e}")
        return ""

    def _load_incident_rca(self) -> str:
        """Load Incident RCA.xlsm as text context."""
        try:
            from openpyxl import load_workbook

            xlsm_path = self.project_root / "Incident RCA.xlsm"
            if xlsm_path.exists():
                wb = load_workbook(xlsm_path, data_only=True)
                context = []
                for sheet_name in wb.sheetnames[:3]:  # Limit to first 3 sheets
                    ws = wb[sheet_name]
                    context.append(f"=== Sheet: {sheet_name} ===")
                    for row_idx, row in enumerate(ws.iter_rows(max_row=20, values_only=True), 1):
                        if any(cell is not None for cell in row):
                            context.append(" | ".join(str(c or "") for c in row))
                    if row_idx > 20:
                        context.append("... (truncated)")
                return "\n".join(context)
        except Exception as e:
            logger.warning(f"Failed to load Incident RCA.xlsm: {e}")
        return ""

    def _load_raw_csvs(self) -> str:
        """Load raw CSV source data to simulate 'no KG, just raw data' scenario."""
        import glob
        csv_dirs = [
            self.project_root / "scenario_batch_42beb1c47273",
            self.project_root / "generated_csv",
        ]
        parts = []
        total_chars = 0
        max_chars = 40000  # ~10K tokens, fits in Claude/GPT context
        for csv_dir in csv_dirs:
            if not csv_dir.exists():
                continue
            for csv_path in sorted(csv_dir.glob("*.csv")):
                try:
                    with open(csv_path, "r") as f:
                        lines = f.readlines()
                    header = lines[0].strip() if lines else ""
                    # Include header + up to 50 data rows per file
                    data_lines = [l.strip() for l in lines[1:51]]
                    chunk = f"=== {csv_path.name} ({len(lines)-1} rows) ===\n{header}\n" + "\n".join(data_lines)
                    if len(lines) > 51:
                        chunk += f"\n... ({len(lines)-51} more rows)"
                    if total_chars + len(chunk) > max_chars:
                        break
                    parts.append(chunk)
                    total_chars += len(chunk)
                except Exception as e:
                    logger.warning(f"Failed to load {csv_path.name}: {e}")
            if total_chars >= max_chars:
                break
        return "\n\n".join(parts)

    def _find_matching_csv_rows(self, question: str) -> str:
        """Find the single CSV row(s) that match entity IDs or customer IDs in the question.

        Simulates what a client without a KG would do: search their CSV for the
        specific record mentioned — they'd only find that one row, not related nodes.
        """
        import re
        # Extract IDs from question
        id_patterns = [
            r"[A-Z]{2,4}-GEN-[A-F0-9]{4,8}",
            r"CUST-\d+",
            r"SITE-[A-Z]{2,3}-[A-Z]{3}-\d+",
            r"ACC-GEN-[A-F0-9]{4,8}",
        ]
        search_ids = set()
        for pat in id_patterns:
            for m in re.finditer(pat, question, re.IGNORECASE):
                search_ids.add(m.group(0).upper())

        # Also extract customer IDs like CUST-3051
        cust_match = re.search(r"CUST-\d+", question, re.IGNORECASE)
        if cust_match:
            search_ids.add(cust_match.group(0).upper())

        if not search_ids:
            return ""

        csv_dirs = [
            self.project_root / "scenario_batch_42beb1c47273",
            self.project_root / "generated_csv",
        ]
        matches = []
        for csv_dir in csv_dirs:
            if not csv_dir.exists():
                continue
            for csv_path in sorted(csv_dir.glob("*.csv")):
                try:
                    with open(csv_path, "r") as f:
                        lines = f.readlines()
                    if not lines:
                        continue
                    header = lines[0].strip()
                    for line in lines[1:]:
                        line_upper = line.upper()
                        if any(sid in line_upper for sid in search_ids):
                            matches.append(f"=== {csv_path.name} ===\n{header}\n{line.strip()}")
                except Exception:
                    continue
        return "\n\n".join(matches[:5])  # Max 5 matching rows

    def _build_prompt(self, question: str, kg_answer: Optional[str] = None, context_mode: str = "kg_context") -> str:
        """Build prompt for Claude/ChatGPT benchmark.

        context_mode:
            'kg_context'    — include full KG traversal answer (same as local model gets)
            'raw_data'      — single matching CSV row only (simulates no-KG: client searches their data)
            'question_only' — single matching CSV row only (same as raw_data — realistic baseline)
        """
        prompt_parts = []

        prompt_parts.append("You are a telecom network analyst.")
        prompt_parts.append("")

        if context_mode == "kg_context":
            if kg_answer:
                prompt_parts.append(f"Data:\n{kg_answer}")
                prompt_parts.append("")
        else:
            # Both 'raw_data' and 'question_only' — just the matching CSV row(s)
            matching_rows = self._find_matching_csv_rows(question)
            if matching_rows:
                prompt_parts.append(f"Data:\n{matching_rows}")
                prompt_parts.append("")
            prompt_parts.append(
                "NOTE: This is the only data record you have. There is no knowledge graph, "
                "no linked records, no relationship mapping. Answer based on this single record only."
            )
            prompt_parts.append("")

        prompt_parts.append(f"Question: {question}")
        prompt_parts.append("")
        prompt_parts.append(
            "Based on the data above, provide the root cause of the issues "
            "and recommendations to fix them. Cite specific IDs from the data."
        )

        return "\n".join(prompt_parts)

    def get_claude_answer(self, question: str, kg_answer: Optional[str] = None, context_mode: str = "kg_context") -> Optional[str]:
        """Get answer from Claude."""
        if not self.claude or not self.settings.claude_configured:
            logger.warning("Claude not configured")
            return None

        try:
            prompt = self._build_prompt(question, kg_answer, context_mode)
            message = self.claude.messages.create(
                model=self.settings.claude_model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            # Handle different content types (text, thinking, etc)
            for content_block in message.content:
                if hasattr(content_block, 'text'):
                    return content_block.text
                elif hasattr(content_block, 'thinking'):
                    # Skip thinking blocks and continue to text
                    continue
            # If no text found, try to get first content's text attribute
            if message.content and hasattr(message.content[0], 'text'):
                return message.content[0].text
            logger.warning(f"No text content in Claude response: {message.content}")
            return None
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return None

    def get_openai_answer(self, question: str, kg_answer: Optional[str] = None, context_mode: str = "kg_context") -> Optional[str]:
        """Get answer from OpenAI."""
        if not self.openai or not self.settings.openai_configured:
            logger.warning("OpenAI not configured")
            return None

        try:
            prompt = self._build_prompt(question, kg_answer, context_mode)
            # Build request parameters - gpt-5 mini doesn't support temperature parameter
            params = {
                "model": self.settings.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": 4096,
            }
            # Only add temperature if model supports it
            if "gpt-5-mini" not in self.settings.openai_model and "gpt-4o-mini" not in self.settings.openai_model:
                params["temperature"] = self.settings.openai_temperature

            response = self.openai.chat.completions.create(**params)
            content = response.choices[0].message.content
            if not content:
                logger.warning(f"OpenAI returned empty content. Response: {response}")
            return content if content else None
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return None

    async def get_both_answers(
        self, question: str, kg_answer: Optional[str] = None, context_mode: str = "kg_context"
    ) -> dict:
        """Get answers from both Claude and OpenAI concurrently."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        executor = ThreadPoolExecutor(max_workers=2)
        loop = asyncio.get_event_loop()

        try:
            claude_task = loop.run_in_executor(executor, self.get_claude_answer, question, kg_answer, context_mode)
            openai_task = loop.run_in_executor(executor, self.get_openai_answer, question, kg_answer, context_mode)

            claude_answer, openai_answer = await asyncio.gather(claude_task, openai_task, return_exceptions=False)

            return {
                "claude": claude_answer,
                "openai": openai_answer,
                "success": claude_answer is not None or openai_answer is not None,
            }
        except Exception as e:
            logger.error(f"Error getting both answers: {e}")
            return {
                "claude": None,
                "openai": None,
                "success": False,
                "error": str(e),
            }
        finally:
            executor.shutdown(wait=False)
