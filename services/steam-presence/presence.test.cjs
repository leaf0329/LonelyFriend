'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {readSnapshot, snapshot} = require('./presence.cjs');
const id = '76561198000000000';

test('Steam persona and localized rich presence use the pinned SDK response shape', async () => {
  const client = {
    getPersonas(ids, callback) {
      assert.deepEqual(ids, [id]);
      callback(null, {[id]: {player_name: '测试玩家', persona_state: 1, gameid: '123', game_played_app_id: 123,
        game_name: '测试游戏', rich_presence_string: '旧活动'}});
    },
    async requestRichPresence(appID, ids, language) {
      assert.equal(appID, 123);
      assert.deepEqual(ids, [id]);
      assert.equal(language, 'schinese');
      return {users: {[id]: {richPresence: {steam_display: '#Activity'}, localizedString: '正在交易'}}};
    },
  };
  const result = await readSnapshot(client, id, 1234);
  assert.equal(result.activity, '正在交易');
  assert.equal(result.activity_known, true);
  assert.equal(result.observed_at, 1234);
});

test('an offline persona clears the game and activity without a rich-presence request', async () => {
  const client = {getPersonas(ids, callback) {callback(null, {[id]: {persona_state: 0, gameid: '0'}});}};
  const result = await readSnapshot(client, id);
  assert.equal(result.game_id, '');
  assert.equal(result.activity, '');
  assert.equal(result.activity_known, true);
});

test('an unavailable activity is marked unknown rather than converted to an empty known activity', async () => {
  const client = {
    getPersonas(ids, callback) {callback(null, {[id]: {persona_state: 1, gameid: '123'}});},
    async requestRichPresence() {return {users: {}};},
  };
  const result = await readSnapshot(client, id);
  assert.equal(result.activity_known, false);
  assert.equal(result.game_id, '123');
});

test('a missing player or persona state is a read failure, not an offline observation', async () => {
  await assert.rejects(readSnapshot({getPersonas(ids, cb) {cb(null, {});}}, id), /did not return/);
  assert.throws(() => snapshot(id, {gameid: '0'}, null, 1), /valid persona state/);
});
