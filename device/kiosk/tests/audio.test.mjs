import { test } from "node:test";
import assert from "node:assert/strict";
import { AudioPlayer } from "../js/audio.js";

class FakeClip {
  constructor(url) {
    this.url = url;
    this.volume = 1;
  }

  play() {
    return Promise.resolve();
  }

  pause() {}
}

test("default volume is 1 when never set", () => {
  const audio = new AudioPlayer(FakeClip);
  audio.play("/audio/cue.mp3");
  assert.equal(audio.current.volume, 1);
});

test("volume is applied to each clip played", () => {
  const audio = new AudioPlayer(FakeClip);
  audio.volume = 0.4;
  audio.play("/audio/cue.mp3");
  assert.equal(audio.current.volume, 0.4);
  audio.volume = 0.9;
  audio.play("/audio/set_start.mp3");
  assert.equal(audio.current.volume, 0.9);
});

test("an unsafe url is never played, so volume is irrelevant", () => {
  const audio = new AudioPlayer(FakeClip);
  audio.volume = 0.2;
  assert.equal(audio.play("https://evil.example/x.mp3"), false);
  assert.equal(audio.current, null);
});

// A clip whose media events the test fires by hand (as the browser would, after play()).
class EventClip extends FakeClip {
  constructor(url) {
    super(url);
    this.onplaying = null;
    this.onended = null;
    this.onpause = null;
    this.onerror = null;
  }
}

test("onSpeaking is true while a clip plays and false when it ends", () => {
  const calls = [];
  const audio = new AudioPlayer(EventClip, { onSpeaking: (on) => calls.push(on) });
  audio.play("/audio/count_3_1.mp3");
  audio.current.onplaying();
  audio.current.onended();
  assert.deepEqual(calls, [true, false]);
});

test("pause and error also end speaking", () => {
  const calls = [];
  const audio = new AudioPlayer(EventClip, { onSpeaking: (on) => calls.push(on) });
  audio.play("/audio/a.mp3");
  audio.current.onplaying();
  audio.current.onpause();
  audio.play("/audio/b.mp3");
  audio.current.onplaying();
  audio.current.onerror();
  assert.deepEqual(calls, [true, false, true, false]);
});

test("a superseded clip's events are ignored", () => {
  const calls = [];
  const audio = new AudioPlayer(EventClip, { onSpeaking: (on) => calls.push(on) });
  audio.play("/audio/a.mp3");
  const first = audio.current;
  first.onplaying();
  audio.play("/audio/b.mp3");
  first.onended?.();
  first.onpause?.();
  assert.deepEqual(calls, [true], "the first clip ending does not stop the newer one");
  audio.current.onplaying();
  audio.current.onended();
  assert.deepEqual(calls, [true, true, false]);
});

test("a clip whose play() is rejected (autoplay blocked) reports not speaking", async () => {
  const calls = [];
  class Blocked extends EventClip { play() { return Promise.reject(new Error("NotAllowedError")); } }
  const audio = new AudioPlayer(Blocked, { onSpeaking: (on) => calls.push(on) });
  audio.play("/audio/a.mp3");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls, [false]);
});

test("onSpeaking is optional", () => {
  const audio = new AudioPlayer(EventClip);
  audio.play("/audio/a.mp3");
  audio.current.onplaying();
  audio.current.onended();
});

test("queued clips play one after another and speaking stays on across the hand-over", () => {
  const calls = [];
  const audio = new AudioPlayer(EventClip, { onSpeaking: (on) => calls.push(on) });
  audio.play("/audio/count_9_1.mp3", { queue: true });
  const first = audio.current;
  first.onplaying();
  assert.equal(audio.play("/audio/last_rep_1.mp3", { queue: true }), true);
  assert.equal(audio.current, first, "the second clip waits for the first");
  first.ended = true;
  first.onpause(); // browsers fire pause right before ended
  first.onended();
  assert.equal(audio.current.url, "/audio/last_rep_1.mp3");
  audio.current.onplaying();
  audio.current.onended();
  assert.deepEqual(calls, [true, true, false]);
  assert.deepEqual(audio.queue, []);
});

test("an errored clip also hands over to the next queued one", () => {
  const audio = new AudioPlayer(EventClip);
  audio.play("/audio/a.mp3", { queue: true });
  audio.play("/audio/b.mp3", { queue: true });
  audio.current.onerror();
  assert.equal(audio.current.url, "/audio/b.mp3");
});

test("the queue keeps at most 2 clips, dropping the oldest", () => {
  const audio = new AudioPlayer(EventClip);
  audio.play("/audio/a.mp3", { queue: true });
  for (const name of ["b", "c", "d"]) audio.play(`/audio/${name}.mp3`, { queue: true });
  assert.deepEqual(audio.queue, ["/audio/c.mp3", "/audio/d.mp3"]);
  audio.current.onended();
  assert.equal(audio.current.url, "/audio/c.mp3");
  audio.current.onended();
  assert.equal(audio.current.url, "/audio/d.mp3");
  audio.current.onended();
  assert.equal(audio.current.url, "/audio/d.mp3");
  assert.deepEqual(audio.queue, []);
});

test("a queued clip plays at once when nothing is playing", () => {
  const audio = new AudioPlayer(EventClip);
  audio.play("/audio/a.mp3");
  audio.current.onended();
  audio.play("/audio/b.mp3", { queue: true });
  assert.equal(audio.current.url, "/audio/b.mp3");
  assert.deepEqual(audio.queue, []);
});

test("a preempting clip stops the current one and clears the queue", () => {
  let paused = 0;
  class Counted extends EventClip { pause() { paused += 1; } }
  const audio = new AudioPlayer(Counted);
  audio.play("/audio/a.mp3", { queue: true });
  audio.play("/audio/b.mp3", { queue: true });
  audio.play("/audio/countdown_3_1.mp3");
  assert.equal(audio.current.url, "/audio/countdown_3_1.mp3");
  assert.deepEqual(audio.queue, []);
  assert.equal(paused, 1);
  audio.current.onended();
  assert.equal(audio.current.url, "/audio/countdown_3_1.mp3", "nothing left to play");
});
