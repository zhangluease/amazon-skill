const test = require('node:test');
const assert = require('node:assert/strict');
const { argumentsFor, responsePins, isDataForSource } = require('../skills/pod-image-selection/scripts/huaban_browser.cjs');

test('late similar responses cannot be attributed to a board page', () => {
  const board = { via: 'recommended_board', provenance: { parent_board_id: '56207432' } };
  assert.equal(isDataForSource('/v3/pins/6119259200/similar', board), false);
  assert.equal(isDataForSource('/v3/boards/56207432', board), true);
  assert.equal(isDataForSource('/v3/boards/1234', board), false);
  const similar = { via: 'similar', provenance: { parent_pin_id: '6119259200' } };
  assert.equal(isDataForSource('/v3/pins/6119259200/similar', similar), true);
  assert.equal(isDataForSource('/v3/pins/1234/similar', similar), false);
});

test('page targets stay within numeric Huaban IDs and bounded counts', () => {
  assert.throws(() => argumentsFor(['--board', '2?private=1', '--out', '/tmp/example']));
  assert.throws(() => argumentsFor(['--query', '狗', '--out', '/tmp/example', '--limit', '1000']));
  const options = argumentsFor(['--query', '狗 动漫', '--out', '/tmp/example']);
  assert.equal(options.headed, false);
  assert.equal(options.query, '狗 动漫');
});

test('candidate extraction handles board previews and excludes private data', () => {
  const pins = responsePins({ boards: [{ board_id: 5, pins: [{ pin_id: 11, is_private: 0 },
    { pin_id: 12, is_private: 1 }] }] });
  assert.deepEqual(pins, [{ pin_id: 11, is_private: 0, board_id: 5 }]);
  assert.deepEqual(responsePins({ error: 'login required', pins: [{ pin_id: 1 }] }), []);
});
