"""Exercise the temporary verification controller over loopback."""

import httpx

from backend_scout.verification_handoff import VerificationHandoffServer


def main() -> None:
    server = VerificationHandoffServer("127.0.0.1", allow_loopback_for_tests=True)
    with server:
        server.publish_frame(b"test-frame")
        with httpx.Client(timeout=2) as client:
            page = client.get(server.url)
            frame = client.get(server.url + "frame.png")
            rejected = client.get(server.url.replace(server.token, "invalid-token"))
            action = client.post(
                server.url + "action",
                json={"kind": "click", "x": 120, "y": 80},
            )
        queued = server.drain_actions()

    if page.status_code != 200 or frame.content != b"test-frame":
        raise SystemExit("Handoff page or frame endpoint failed")
    if rejected.status_code != 404 or action.status_code != 202:
        raise SystemExit("Handoff authorization or action endpoint failed")
    if len(queued) != 1 or queued[0].kind != "click":
        raise SystemExit("Handoff action queue failed")
    print("Tailscale handoff black-box test passed over the local test binding.")


if __name__ == "__main__":
    main()
