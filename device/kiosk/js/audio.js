// Plays Mira's lines. Only same-origin /audio/*.mp3 paths are allowed (audio_url comes from the server, which
// may carry AI-chosen ids); one clip at a time, the newest wins. onSpeaking(bool) reports whether Mira is
// audibly talking (drives the mascot's talk animation); events from a superseded clip are ignored.

const SAFE = /^\/audio\/[A-Za-z0-9._-]+\.mp3$/;

export function isSafeAudioUrl(url) {
  return typeof url === "string" && SAFE.test(url) && !url.includes("..");
}

export class AudioPlayer {
  constructor(AudioImpl = globalThis.Audio, { onSpeaking } = {}) {
    this.AudioImpl = AudioImpl;
    this.onSpeaking = typeof onSpeaking === "function" ? onSpeaking : () => {};
    this.current = null;
    this.volume = 1;
  }

  play(url) {
    if (!isSafeAudioUrl(url)) return false;
    const previous = this.current;
    const clip = new this.AudioImpl(url);
    clip.volume = this.volume;
    // Swap first so the old clip's pause event (sync in tests, async in browsers) is already stale.
    this.current = clip;
    const report = (on) => () => { if (this.current === clip) this.onSpeaking(on); };
    clip.onplaying = report(true);
    clip.onended = report(false);
    clip.onpause = report(false);
    clip.onerror = report(false);
    previous?.pause();
    // Autoplay blocked outside kiosk mode: captions still show, and Mira stops "talking".
    clip.play().catch(report(false));
    return true;
  }
}
