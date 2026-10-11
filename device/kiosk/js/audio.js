// Plays Mira's lines. Only same-origin /audio/*.mp3 paths are allowed (audio_url comes from the server, which
// may carry AI-chosen ids). One clip at a time: play(url) preempts (stops the current clip and clears the
// queue); play(url, { queue: true }) waits for the current clip, keeping at most MAX_QUEUED waiting clips (the
// oldest is dropped when full). onSpeaking(bool) reports whether Mira is audibly talking (drives the mascot's
// talk animation); events from a superseded clip are ignored.

const SAFE = /^\/audio\/[A-Za-z0-9._-]+\.mp3$/;
export const MAX_QUEUED = 2;

export function isSafeAudioUrl(url) {
  return typeof url === "string" && SAFE.test(url) && !url.includes("..");
}

export class AudioPlayer {
  constructor(AudioImpl = globalThis.Audio, { onSpeaking } = {}) {
    this.AudioImpl = AudioImpl;
    this.onSpeaking = typeof onSpeaking === "function" ? onSpeaking : () => {};
    this.current = null;
    this.playing = false; // the current clip has not ended, errored or been paused
    this.queue = [];
    this.volume = 1;
  }

  play(url, { queue = false } = {}) {
    if (!isSafeAudioUrl(url)) return false;
    if (queue && this.current !== null && this.playing) {
      this.queue.push(url);
      if (this.queue.length > MAX_QUEUED) this.queue.shift();
      return true;
    }
    this.queue = [];
    this.start(url);
    return true;
  }

  start(url) {
    const previous = this.current;
    const clip = new this.AudioImpl(url);
    clip.volume = this.volume;
    // Swap first so the old clip's pause event (sync in tests, async in browsers) is already stale.
    this.current = clip;
    this.playing = true;
    const playing = () => { if (this.current === clip) this.onSpeaking(true); };
    // A finished clip hands over to the next queued one; speaking stays true across that hand-over.
    const done = () => {
      if (this.current !== clip) return;
      this.playing = false;
      const next = this.queue.shift();
      if (next !== undefined) this.start(next);
      else this.onSpeaking(false);
    };
    clip.onplaying = playing;
    clip.onended = done;
    clip.onerror = done;
    // Browsers fire `pause` just before `ended` at the end of a clip; that one is left to `ended` (the queue goes on).
    clip.onpause = () => {
      if (this.current !== clip || clip.ended === true) return;
      this.playing = false;
      this.queue = [];
      this.onSpeaking(false);
    };
    previous?.pause();
    // Autoplay blocked outside kiosk mode: captions still show, and Mira stops "talking".
    clip.play().catch(done);
  }
}
