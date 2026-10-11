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
