// Plays Mira's lines. Only same-origin /audio/*.mp3 paths are allowed (audio_url comes from the server, which
// may carry AI-chosen ids); one clip at a time, the newest wins.

const SAFE = /^\/audio\/[A-Za-z0-9._-]+\.mp3$/;

export function isSafeAudioUrl(url) {
  return typeof url === "string" && SAFE.test(url) && !url.includes("..");
}

export class AudioPlayer {
  constructor(AudioImpl = globalThis.Audio) {
    this.AudioImpl = AudioImpl;
    this.current = null;
  }

  play(url) {
    if (!isSafeAudioUrl(url)) return false;
    this.current?.pause();
    const clip = new this.AudioImpl(url);
    this.current = clip;
    clip.play().catch(() => { /* autoplay blocked outside kiosk mode: captions still show */ });
    return true;
  }
}
