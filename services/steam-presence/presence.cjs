'use strict';

const fs = require('node:fs');
const path = require('node:path');

function emit(value) {
  process.stdout.write(JSON.stringify(value) + '\n');
}

function writePrivate(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true, mode: 0o700});
  const temporary = file + '.tmp';
  fs.writeFileSync(temporary, JSON.stringify(value), {mode: 0o600});
  fs.renameSync(temporary, file);
}

function networkOptions(proxy) {
  if (!proxy) return {};
  const protocol = new URL(proxy).protocol;
  if (protocol === 'http:' || protocol === 'https:') return {httpProxy: proxy};
  if (protocol === 'socks5:' || protocol === 'socks5h:') return {socksProxy: proxy};
  throw new Error('Use an HTTP or SOCKS5 proxy');
}

function snapshot(steamID, persona, presence, observedAt) {
  if (!Number.isInteger(persona.persona_state) || persona.persona_state < 0 || persona.persona_state > 7) {
    throw new Error('Steam did not return a valid persona state');
  }
  const rawGameID = String(persona.gameid ?? persona.game_played_app_id ?? '0');
  const gameID = rawGameID === '0' ? '' : rawGameID;
  const text = presence?.localizedString || presence?.richPresence?.status || persona.rich_presence_string;
  return {
    steam_id: steamID,
    name: String(persona.player_name || steamID).slice(0, 150),
    persona_state: persona.persona_state,
    game_id: gameID,
    game_name: gameID ? String(persona.game_name || `游戏 ${gameID}`).slice(0, 200) : '',
    activity: gameID && text ? String(text).trim().slice(0, 1200) : '',
    activity_known: !gameID || Boolean(text),
    observed_at: observedAt,
  };
}

async function readSnapshot(client, steamID, observedAt = Date.now() / 1000) {
  const personas = await new Promise((resolve, reject) => {
    client.getPersonas([steamID], (error, values) => error ? reject(error) : resolve(values));
  });
  const persona = personas[steamID];
  if (!persona) throw new Error('Steam did not return the monitored player');
  const appID = Number(persona.game_played_app_id || persona.gameid || 0);
  let presence;
  if (Number.isInteger(appID) && appID > 0 && appID <= 0xffffffff) {
    try {
      const response = await client.requestRichPresence(appID, [steamID], 'schinese');
      presence = response.users[steamID];
    } catch (error) {
      // A missing rich-presence response must not erase a last known activity.
      emit({type: 'health', status: 'activity_unavailable', code: error.eresult || null});
    }
  }
  return snapshot(steamID, persona, presence, observedAt);
}

async function login(config, directory) {
  const {LoginSession, EAuthTokenPlatformType} = require('steam-session');
  const session = new LoginSession(EAuthTokenPlatformType.SteamClient, {
    ...networkOptions(config.proxy),
    machineFriendlyName: 'LonelyFriend Steam status monitor',
  });
  session.loginTimeout = 300000;
  session.on('authenticated', () => {
    writePrivate(path.join(directory, 'login.json'), {
      steam_id: session.steamID.getSteamID64(), refresh_token: session.refreshToken,
    });
    emit({type: 'authenticated'});
    process.exit(0);
  });
  session.on('timeout', () => {
    emit({type: 'health', status: 'login_expired'});
    process.exit(2);
  });
  session.on('error', (error) => {
    emit({type: 'health', status: 'login_error', code: error.eresult || null});
    process.exit(1);
  });
  const challenge = await session.startWithQR();
  writePrivate(path.join(directory, 'login-qr.json'), {url: challenge.qrChallengeUrl});
  emit({type: 'qr_ready'});
}

function watch(config, directory) {
  const SteamUser = require('steam-user');
  const credentials = JSON.parse(fs.readFileSync(path.join(directory, 'login.json'), 'utf8'));
  const client = new SteamUser({
    ...networkOptions(config.proxy),
    dataDirectory: path.join(directory, 'cache'),
    language: 'schinese', webCompatibilityMode: true,
    enablePicsCache: false, enableUserDataCache: false,
  });
  let timer;
  let reading = false;
  let connected = false;
  let stopping = false;
  async function poll() {
    if (reading || !connected || stopping) return;
    reading = true;
    try {
      const state = await readSnapshot(client, config.steam_id);
      if (connected && !stopping) emit({type: 'state', ...state});
    } catch (error) {
      emit({type: 'health', status: 'poll_error', code: error.eresult || null});
    } finally {
      reading = false;
    }
  }
  client.on('loggedOn', () => {
    connected = true;
    emit({type: 'health', status: 'connected'});
    clearInterval(timer);
    timer = setInterval(poll, config.poll_seconds * 1000);
    poll();
  });
  client.on('disconnected', () => {
    connected = false;
    emit({type: 'health', status: 'disconnected'});
  });
  client.on('error', (error) => {
    connected = false;
    emit({type: 'health', status: 'connection_error', code: error.eresult || null});
  });
  client.on('steamGuard', () => {
    emit({type: 'health', status: 'login_required'});
    process.exit(2);
  });
  function stop() {
    stopping = true;
    clearInterval(timer);
    client.logOff();
    process.exit(0);
  }
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  client.logOn({
    refreshToken: credentials.refresh_token,
    logonID: 0x4c464600, machineName: 'LonelyFriend Steam monitor',
  });
}

async function main() {
  const [operation, configPath] = process.argv.slice(2);
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
  if (!/^7656119\d{10}$/.test(config.steam_id) || !Number.isInteger(config.poll_seconds) || config.poll_seconds < 30) {
    throw new Error('Invalid Steam monitor configuration');
  }
  const directory = path.join(path.dirname(configPath), 'steam-presence');
  if (operation === 'login') await login(config, directory);
  else if (operation === 'watch') watch(config, directory);
  else throw new Error('Use login or watch');
}

if (require.main === module) {
  main().catch((error) => {
    emit({type: 'health', status: 'startup_error', code: error.eresult || null});
    process.exitCode = 1;
  });
}
module.exports = {snapshot, readSnapshot};
