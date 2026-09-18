"""Focused local processing regressions; no model or server is required."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agents.cataloguer import catalogue_transcripts
from agents.deidentifier import discover_pii_entities
from agents.transcriber import transcribe_media, ExtractedTranscript


class LocalPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_ollama_metrics_use_nanoseconds_and_missing_is_unknown(self):
        from utils.batch import discover_names
        for telemetry, expected in [({'prompt_eval_count': 64, 'eval_count': 48, 'eval_duration': 2_000_000_000}, 24), ({}, None)]:
            def respond(request):
                return httpx.Response(200, json={'done': True, 'done_reason': 'stop',
                    'message': {'content': '{"entities": []}'}, **telemetry})
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            metrics = []
            with patch('utils.agent_config.httpx.AsyncClient', return_value=client):
                await discover_names('No names.', model='test', base_url='http://localhost:11434', metrics_callback=metrics.append)
            self.assertEqual(len(metrics), 1)
            self.assertEqual(metrics[0]['tokens_per_second'], expected)
            self.assertGreater(metrics[0]['request_seconds'], 0)

    async def test_total_deadline_cancels_a_stalled_request(self):
        cancelled = asyncio.Event()
        async def respond(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("utils.agent_config.httpx.AsyncClient", return_value=client), patch.dict(
            "os.environ", {"MODEL_TIMEOUT_SECONDS": "0.02"}
        ):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                await discover_pii_entities({}, backend="ollama")
        self.assertTrue(cancelled.is_set())

    async def test_docx_extraction_preserves_body_and_tables(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.docx"
            doc = Document()
            doc.add_paragraph("Patient: Alex Example")
            doc.add_table(rows=1, cols=1).cell(0, 0).text = "Doctor: Robin Test"
            doc.save(path)
            with patch("agents.transcriber.Agent", side_effect=AssertionError("Model used to copy DOCX")):
                result = await transcribe_media(str(path), backend="ollama")
            self.assertEqual(result["items"][0]["content"], "Patient: Alex Example\nDoctor: Robin Test")

    async def test_unavailable_server_does_not_fall_back_to_cloud(self):
        def respond(request):
            raise httpx.ConnectError("offline", request=request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("utils.agent_config.httpx.AsyncClient", return_value=client), patch(
            "utils.agent_config.Agent", side_effect=AssertionError("Cloud fallback")
        ):
            with self.assertRaisesRegex(RuntimeError, "Cannot reach Ollama"):
                await discover_pii_entities({}, backend="ollama")

    async def test_text_extraction_preserves_content_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.txt"
            text = "Patient: Alex Example.\n\nClinician: Dr. Robin Test.\n"
            path.write_text(text)
            with patch("agents.transcriber.Agent", side_effect=AssertionError("Model used to copy text")):
                result = await transcribe_media(str(path), backend="ollama")
            ExtractedTranscript.model_validate(result)
            self.assertEqual(result["items"][0]["content"], text)
            self.assertEqual(result["filename"], "record.txt")

    async def test_local_stages_use_native_schema_and_selected_endpoint(self):
        chronology = {
            "patient_summary": "Alex Example attended.", "categories_found": ["Intake"],
            "chronology": [{"timestamp": "line 1", "category": "Intake",
                            "source_file": "record.txt", "speaker": "Document",
                            "event_details": "Patient: Alex Example."}],
        }
        entities = {"entities": [{"canonical_name": "Alex Example", "entity_type": "PATIENT",
                                  "relationship_context": "Subject", "variations": ["Alex Example"]}]}
        requests = []

        def respond(request):
            requests.append(request)
            payload = json.loads(request.content)
            schema = payload["format"]
            data = chronology if schema["title"] == "UnifiedChronology" else entities
            return httpx.Response(200, json={"done": True, "done_reason": "stop",
                                           "message": {"content": json.dumps(data)}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("utils.agent_config.httpx.AsyncClient", return_value=client):
            result = await catalogue_transcripts([], backend="ollama", ollama_base_url="http://local.test:1234/v1")
        self.assertEqual(result, chronology)
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("utils.agent_config.httpx.AsyncClient", return_value=client):
            result = await discover_pii_entities(chronology, backend="ollama", ollama_base_url="http://local.test:1234/v1")
        self.assertEqual(result, entities["entities"])
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(str(request.url), "http://local.test:1234/api/chat")
            payload = json.loads(request.content)
            self.assertFalse(payload["think"])
            self.assertNotIn("tools", payload)
            self.assertIn("$defs", payload["format"])

    async def test_bad_outputs_fail_once_without_silent_success(self):
        for response in (
            {"done": True, "done_reason": "stop", "message": {"content": "not json"}},
            {"done": True, "done_reason": "stop", "message": {"content": '{"entities":[{}]}'}},
            {"done": True, "done_reason": "length", "message": {"content": '{"entities":[]}'}},
            {"done": False, "message": {"content": '{"entities":[]}'}},
        ):
            with self.subTest(response=response):
                calls = []
                def respond(request):
                    calls.append(request)
                    return httpx.Response(200, json=response)
                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with patch("utils.agent_config.httpx.AsyncClient", return_value=client):
                    with self.assertRaisesRegex(ValueError, "Ollama"):
                        await discover_pii_entities({}, backend="ollama")
                self.assertEqual(len(calls), 1)

    async def test_network_timeout_is_actionable_and_not_retried(self):
        async def respond(request):
            raise httpx.ReadTimeout("test", request=request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("utils.agent_config.httpx.AsyncClient", return_value=client):
            with self.assertRaisesRegex(TimeoutError, "Ollama"):
                await discover_pii_entities({}, backend="ollama")


class PdfRedactionTest(unittest.TestCase):
    def test_pdf_overlapping_aliases_and_no_match_do_not_trigger_ocr(self):
        import pymupdf
        from utils.document_editor import deidentify_pdf
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.pdf", Path(directory) / "output.pdf"
            with pymupdf.open() as doc:
                doc.new_page().insert_text((40, 60), "Alex Example attended.")
                doc.save(source)
            with patch("utils.document_editor._ocr_pdf", side_effect=AssertionError("Unnecessary OCR")):
                self.assertTrue(deidentify_pdf(str(source), str(output), {"Alex Example": "PATIENT_TEST", "Alex": "PATIENT_TEST"}))
                with pymupdf.open(output) as doc:
                    self.assertEqual(doc[0].get_text().count("PATIENT_TEST"), 1)
                output.unlink()
                self.assertTrue(deidentify_pdf(str(source), str(output), {}))

    def test_scanned_pdf_ocr_failure_does_not_export_original(self):
        import pymupdf
        from utils.document_editor import deidentify_pdf
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "scan.pdf", Path(directory) / "output.pdf"
            with pymupdf.open() as original:
                page = original.new_page()
                page.insert_text((40, 60), "Alex Example")
                pixmap = page.get_pixmap()
                with pymupdf.open() as scan:
                    scan.new_page().insert_image(page.rect, pixmap=pixmap)
                    scan.save(source)
            with patch("utils.document_editor._ocr_pdf", side_effect=RuntimeError("OCR unavailable")):
                with self.assertRaisesRegex(RuntimeError, "OCR unavailable"):
                    deidentify_pdf(str(source), str(output), {"Alex Example": "PATIENT_TEST"})
            self.assertFalse(output.exists())

    def test_styles_are_extracted_once_per_page_without_changing_redaction(self):
        import pymupdf
        from utils.document_editor import _redact_pdf
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            output = Path(directory) / "output.pdf"
            with pymupdf.open() as doc:
                for _ in range(2):
                    page = doc.new_page()
                    for line in range(3):
                        page.insert_text((40, 40 + line * 30), "Alex Example", fontsize=11)
                doc.save(source)
            extracted = []
            original = pymupdf.Page.get_text
            def get_text(page, *args, **kwargs):
                if args and args[0] == "dict":
                    extracted.append(page.number)
                return original(page, *args, **kwargs)
            with patch.object(pymupdf.Page, "get_text", get_text):
                count = _redact_pdf(str(source), str(output), {"Alex Example": "PATIENT_ABC"})
            self.assertEqual(count, 6)
            self.assertEqual(extracted, [0, 1])
            with pymupdf.open(output) as doc:
                for xref in range(1, doc.xref_length()):
                    if doc.xref_is_stream(xref):
                        stream = doc.xref_stream(xref)
                        self.assertNotIn(b"Alex Example", stream)
                        self.assertNotIn(b"416c6578204578616d706c65", stream.lower())
                for page in doc:
                    self.assertNotIn("Alex Example", page.get_text())
                    self.assertEqual(page.get_text().count("PATIENT_ABC"), 3)


if __name__ == "__main__":
    unittest.main()
