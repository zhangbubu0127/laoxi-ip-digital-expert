import json, os, subprocess, time, glob

def send_reply(chat_id: str, text: str) -> None:
    subprocess.run([
        "lark-cli", "im", "+messages-send",
        "--chat-id", chat_id,
        "--text", text,
    ], check=True, capture_output=True)

def start_listener(output_dir: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["lark-cli", "event", "consume", "im.message.receive_v1", "--output-dir", output_dir],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def poll_events(output_dir: str) -> list[dict]:
    events = []
    for path in sorted(glob.glob(os.path.join(output_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            events.append(json.load(f))
        os.unlink(path)
    return events

def run_loop(output_dir: str, poll_interval: float = 2.0) -> None:
    os.makedirs(output_dir, exist_ok=True)
    listener = start_listener(output_dir)
    try:
        while True:
            for event in poll_events(output_dir):
                from pipe import parse_inbound
                from skin.identity import resolve_role
                from brain.controller import handle_message
                msg = parse_inbound(event)
                msg.sender_role = resolve_role(msg.sender_open_id)
                for reply in handle_message(msg):
                    send_reply(reply.chat_id, reply.text)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        listener.terminate()

if __name__ == "__main__":
    import sys
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "events"
    run_loop(out_dir)
