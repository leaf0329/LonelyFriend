import net from 'node:net';

function canConnect({ ip, port }) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: ip, port });
    const finish = (connected) => {
      socket.destroy();
      resolve(connected);
    };
    socket.setTimeout(2000);
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    socket.once('timeout', () => finish(false));
    socket.once('close', () => finish(false));
  });
}

export async function selectHighwayServer(addresses) {
  const seen = new Set();
  for (const address of addresses) {
    const key = `${address.ip}:${address.port}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (await canConnect(address)) return address;
  }
  throw new Error('[Highway] No reachable QQ upload server');
}
