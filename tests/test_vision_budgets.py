import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from veda.vision import LocalOllamaVisionProvider, StructuredGameState


class VisionBudgetTests(unittest.TestCase):
    def test_explicit_context_leaves_a_bounded_response_budget(self):
        requests = []
        def provider_request(body):
            requests.append(json.loads(body))
            return json.dumps({'message': {'content': json.dumps(asdict(
                StructuredGameState('TITLE', 1.0)))}, 'done_reason': 'stop'}).encode()
        with TemporaryDirectory() as directory:
            image = Path(directory) / 'fixture.png'
            image.write_bytes(b'not a gameplay frame; injected provider contract test')
            state = LocalOllamaVisionProvider(request=provider_request).observe(image)
        self.assertEqual(state.screen_type, 'TITLE')
        self.assertEqual(requests[0]['options']['num_ctx'], 8192)
        self.assertEqual(requests[0]['options']['num_predict'], 2048)

    def test_truncated_answer_remains_unknown_even_if_it_parses(self):
        response = json.dumps({'message': {'content': json.dumps({
            'screen_type': 'COMBAT', 'confidence': 1.0})}, 'done_reason': 'length'}).encode()
        with TemporaryDirectory() as directory:
            image = Path(directory) / 'fixture.png'
            image.write_bytes(b'offline injected response')
            provider = LocalOllamaVisionProvider(request=lambda body: response)
            state = provider.observe(image)
        self.assertEqual(state.screen_type, 'UNKNOWN')
        self.assertEqual(provider.last_error, 'IncompleteVisionResponse')


if __name__ == '__main__':
    unittest.main()
