import assert from 'node:assert/strict';
import net from 'node:net';
import { once } from 'node:events';
import test from 'node:test';
import { selectHighwayServer } from '../extensions/javascript/napcat-highway-server.mjs';

async function server() {
  const listener = net.createServer((socket) => socket.destroy());
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  return { listener, address: { ip: '127.0.0.1', port: listener.address().port } };
}

async function stop(listener) {
  await new Promise((resolve, reject) => listener.close((error) => error ? reject(error) : resolve()));
}

test('unreachable selected node falls through to a reachable QQ candidate', async () => {
  const unavailable = await server();
  await stop(unavailable.listener);
  const available = await server();
  try {
    assert.deepEqual(await selectHighwayServer([unavailable.address, available.address]), available.address);
  } finally {
    await stop(available.listener);
  }
});

test('a reachable selected node keeps its priority and does not probe alternatives', async () => {
  const preferred = await server();
  const alternative = await server();
  let connections = 0;
  alternative.listener.on('connection', () => connections++);
  try {
    assert.deepEqual(await selectHighwayServer([preferred.address, alternative.address]), preferred.address);
    assert.equal(connections, 0);
  } finally {
    await stop(preferred.listener);
    await stop(alternative.listener);
  }
});

test('all unreachable upload nodes produce an explicit failure', async () => {
  const unavailable = await server();
  await stop(unavailable.listener);
  await assert.rejects(selectHighwayServer([unavailable.address]), /No reachable QQ upload server/);
});
