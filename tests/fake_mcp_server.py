"""A minimal MCP server over stdio, used to test the bridge. No dependencies."""
import json
import sys

TOOLS = [
    {
        "name": "word_count",
        "description": "Count words in a text.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
    {
        "name": "reverse",
        "description": "Reverse a string.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
]


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            continue  # notification
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments") or {}
            text = str(args.get("text", ""))
            if name == "word_count":
                out = str(len(text.split()))
            elif name == "reverse":
                out = text[::-1]
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                  "error": {"code": -32601, "message": f"unknown tool {name}"}}), flush=True)
                continue
            result = {"content": [{"type": "text", "text": out}], "isError": False}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                              "error": {"code": -32601, "message": "method not found"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}), flush=True)


if __name__ == "__main__":
    main()
