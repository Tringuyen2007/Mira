// Minimal WHEP client: camera video travels over WebRTC, not the pose WebSocket.  The caller supplies the
// therapist bearer token; the media relay validates it independently of the Mira API before returning an SDP answer.
export async function attachWhep(video, url, token) {
  const peer = new RTCPeerConnection();
  peer.addTransceiver("video", { direction: "recvonly" });
  peer.ontrack = ({ streams }) => { video.srcObject = streams[0]; video.play().catch(() => {}); };
  const offer = await peer.createOffer();
  await peer.setLocalDescription(offer);
  await new Promise((resolve) => {
    if (peer.iceGatheringState === "complete") return resolve();
    peer.addEventListener("icegatheringstatechange", () => peer.iceGatheringState === "complete" && resolve(), { once: true });
  });
  const response = await fetch(url, { method: "POST", body: peer.localDescription.sdp,
    headers: { "Content-Type": "application/sdp", Accept: "application/sdp", Authorization: `Bearer ${token}` } });
  if (!response.ok) { peer.close(); throw new Error("camera video unavailable"); }
  await peer.setRemoteDescription({ type: "answer", sdp: await response.text() });
  return () => { video.srcObject = null; peer.close(); };
}
