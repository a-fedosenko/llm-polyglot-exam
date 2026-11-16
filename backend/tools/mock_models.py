"""A stand-in OpenAI-compatible endpoint, for driving the UI without spending.

Every measurement here is fiction — it answers by *role*, from a script, so the
page can be walked end to end with no key and no budget. That is what it is for:
UI work, demos, and checking that a change to the traversal still reaches a
verdict. It is not a test double for the method, and nothing it produces is a
measurement of anything.

    python backend/tools/mock_models.py            # listens on 127.0.0.1:9911

Then point a models.json at it:

    {"models": [
      {"model_title": "Mock A (engine)", "model_id": "acme-mock-a",
       "api_key": "x", "v1_endpoint": "http://127.0.0.1:9911/v1"},
      {"model_title": "Mock B (reader)", "model_id": "bravo-mock-b",
       "api_key": "x", "v1_endpoint": "http://127.0.0.1:9911/v1"},
      {"model_title": "Mock C (judge)",  "model_id": "cedar-mock-c",
       "api_key": "x", "v1_endpoint": "http://127.0.0.1:9911/v1"}
    ]}

The ids are from three different families on purpose: the panel guard drops the
producer's whole family, so three mocks sharing a prefix would leave no eligible
control model and the dialect battery would stop at `indeterminate`.
"""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PT = ("O ônibus atrasou por causa da chuva forte. Ela chegou ao escritório "
      "depois das nove. O trânsito estava parado.")
PT_PT = PT.replace("ônibus", "autocarro")
BACK = ("The bus was late because of heavy rain. She reached the office after "
        "nine. The traffic was at a standstill.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN001 - quiet by default
        pass

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = next((m["content"] for m in msgs if m["role"] == "user"), "")
        out = {"choices": [{"message": {"content": self.answer(system, user)},
                            "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 100, "completion_tokens": 60}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def answer(self, system: str, user: str) -> str:
        # Matched on the longest phrase unique to each prompt. The separation
        # prompt also contains the words "which regional variety", so a shorter
        # needle for identification silently swallows it — which is exactly the
        # bug this comment exists to stop someone reintroducing.
        if "rewrite the text the user gives you" in system:
            return PT_PT if "Portugal" in system else PT
        if "You are a native speaker of" in system:
            return PT_PT if "Portugal" in system else PT
        if "which regional variety a piece of text" in system:
            return json.dumps({"answer": "Brazil",
                               "markers": [{"text": "ônibus", "why": "Brazilian"}],
                               "confidence": "high"})
        if "shown two texts" in system:
            a, b = user.split("TEXT A:", 1)[1].split("TEXT B:", 1)
            if a.strip() == b.strip():
                # The identity control. The only correct answer.
                return json.dumps({"a": "indistinguishable",
                                   "b": "indistinguishable",
                                   "markers": [], "confidence": "high"})
            return json.dumps({"a": "Portugal" if "autocarro" in a else "Brazil",
                               "b": "Portugal" if "autocarro" in b else "Brazil",
                               "markers": [], "confidence": "high"})
        if user.startswith("Translate the following text into"):
            return BACK
        if "checking whether a piece of text contains" in user:
            n = len(re.findall(r"^\d+\.", user, re.M))
            return json.dumps({"verdicts": ["present"] * n})
        return "ok"


if __name__ == "__main__":
    print("mock models on http://127.0.0.1:9911/v1 — nothing here is a measurement")
    HTTPServer(("127.0.0.1", 9911), Handler).serve_forever()
