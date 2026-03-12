import asyncio
import json
import time

async def test_ws():
    try:
        import websockets
    except ImportError:
        print("websockets not installed, skipping test")
        return

    sid = "00000000-test-0000-0000-000000000001"
    uri = f"ws://localhost:8080/ws/voice/{sid}"
    print(f"Connecting to {uri} ...")
    t0 = time.perf_counter()
    try:
        async with websockets.connect(uri) as ws:
            print("Connected OK")
            # init session
            await ws.send(json.dumps({"type": "init", "patient_name": "Test User"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            print(f"  [{msg['type']}] {str(msg.get('message', ''))[:80]}")

            # send a text message and wait for the full pipeline response
            await ws.send(json.dumps({"type": "text_message", "text": "list available doctors"}))
            print("  Waiting for agent response (up to 60s) ...")
            for _ in range(200):
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=35))
                    t = m.get("type", "?")
                    elapsed = round(time.perf_counter() - t0, 1)
                    if t == "audio_chunk":
                        print(f"  [{t}] <audio bytes> (+{elapsed}s)")  
                    else:
                        detail = str(m.get("text", m.get("calls", m.get("message", ""))))[:100]
                        print(f"  [{t}] {detail} (+{elapsed}s)")
                    if t == "audio_end":
                        print(f"\nPipeline complete! Total: {elapsed}s")
                        break
                    if t == "error":
                        print(f"\nAgent error (config issue): {m.get('message')}")
                        break
                except asyncio.TimeoutError:
                    elapsed = round(time.perf_counter() - t0, 1)
                    print(f"  (no message for 35s at +{elapsed}s — giving up)")
                    break
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR: {e}")

asyncio.run(test_ws())
