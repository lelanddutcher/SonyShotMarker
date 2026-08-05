/*
 * node-reader.js — the Reader interface backed by Node's fs, for the test suite.
 *
 * The plugin ships a UXP-backed reader with the same two methods. Keeping the interface
 * this thin is the whole point: every byte of parsing logic is exercised under Node
 * against real Sony footage, and Premiere only ever swaps in a different way to fetch
 * `length` bytes at `position`.
 */
const fs = require("fs");

function createNodeFileReader(path) {
  let fd = null;
  let bytes = null;
  return {
    path,
    async size() {
      if (bytes === null) bytes = fs.statSync(path).size;
      return bytes;
    },
    async read(position, length) {
      if (fd === null) fd = fs.openSync(path, "r");
      const buf = Buffer.allocUnsafe(length);
      const n = fs.readSync(fd, buf, 0, length, position);
      return new Uint8Array(buf.buffer, buf.byteOffset, n);
    },
    close() {
      if (fd !== null) { fs.closeSync(fd); fd = null; }
    }
  };
}

/** A reader over an in-memory buffer, for synthetic fixtures. */
function createBufferReader(u8) {
  return {
    async size() { return u8.length; },
    async read(position, length) {
      return u8.subarray(position, Math.min(position + length, u8.length));
    }
  };
}

/**
 * Wraps a reader and records every read, so tests can assert we never pulled the media
 * payload into memory. This is the guardrail on the "reads a 100 GB card safely" claim.
 */
function instrument(reader) {
  const reads = [];
  return {
    reads,
    totalBytesRead() { return reads.reduce((a, r) => a + r.length, 0); },
    maxSingleRead() { return reads.reduce((a, r) => Math.max(a, r.length), 0); },
    async size() { return reader.size(); },
    async read(position, length) {
      reads.push({ position, length });
      return reader.read(position, length);
    }
  };
}

module.exports = { createNodeFileReader, createBufferReader, instrument };
