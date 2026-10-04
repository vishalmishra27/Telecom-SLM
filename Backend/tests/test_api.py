import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from openpyxl import Workbook

from app.config import PROJECT_ROOT
from app.ingestion_service import ExcelIngestionService
from app.main import chat, graph, health
from app.models import ChatRequest


class ApiTests(unittest.TestCase):
    def test_health_uses_empty_dynamic_state_without_credentials(self):
        self.assertEqual(health()["status"], "healthy")
        self.assertIn(health()["data"], {"empty", "neo4j"})

    def test_graph_returns_dynamic_payload(self):
        payload = graph(limit=180)
        node_ids = {node.id for node in payload.nodes}
        self.assertTrue(all(
            rel.source in node_ids and rel.target in node_ids
            for rel in payload.relationships
        ))

    def test_follow_up_keeps_conversation_without_static_scenario(self):
        first = chat(ChatRequest(message="Which incidents require remediation?"))
        second = chat(ChatRequest(
            message="What action should I follow?",
            conversation_id=first.conversation_id,
        ))
        self.assertEqual(second.conversation_id, first.conversation_id)
        self.assertIn("Summary", second.answer)

    def test_incident_kind_question_uses_aggregate_intent(self):
        response = chat(ChatRequest(message="what kind of incidents are occured?"))
        self.assertEqual(response.intent, "incident_kind_summary")
        self.assertIn("Summary", response.answer)

    def test_specific_operational_question_uses_ranked_incident_example(self):
        response = chat(ChatRequest(
            message="Why were barring/unbarring orders for Postpaid, Broadband and Black stuck on 4 Apr 2025, and how was it fixed?"
        ))
        self.assertEqual(response.intent, "incident_example")
        self.assertIn("Summary", response.answer)

    def test_excel_ingestion_creates_neo4j_csvs(self):
        workbook = Workbook()
        incident = workbook.active
        incident.title = "APPROVED CRs"
        incident.append(["canonical_id", "name", "scenario_id"])
        incident.append(["CR-1", "Approved change", "SCN-1"])
        alarm = workbook.create_sheet("Alarm")
        alarm.append(["canonical_id", "name", "scenario_id", "escalatesTo"])
        alarm.append(["ALM-1", "Alarm one", "SCN-1", "CR-1"])
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        with TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            service = ExcelIngestionService(PROJECT_ROOT, PROJECT_ROOT / "schema.json")
            result = service.ingest(
                str(directory),
                [SimpleNamespace(filename="sample.xlsx", file=buffer)],
            )

            self.assertEqual(result.node_count, 2)
            self.assertEqual(result.relationship_count, 1)
            self.assertEqual(result.warnings, [])
            self.assertTrue(Path(result.nodes_path).exists())
            self.assertTrue(Path(result.relationships_path).exists())
            self.assertIn("nodes.csv", result.nodes_path)
            self.assertIn("relationships.csv", result.relationships_path)


if __name__ == "__main__":
    unittest.main()
