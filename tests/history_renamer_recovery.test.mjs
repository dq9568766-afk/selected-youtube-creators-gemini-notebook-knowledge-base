import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_RECOVERY_ATTEMPTS,
  runWithRecovery,
  recoveryReason,
} from '../extensions/history-renamer/recovery.js';

test('retries a recoverable page failure with a bounded recovery budget', async()=>{
  const attempts=[];
  let calls=0;
  const result=await runWithRecovery(
    async()=>{
      calls++;
      if(calls<3) throw new Error('ui_changed');
      return 'verified';
    },
    async(reason,attempt)=>attempts.push({reason,attempt}),
  );
  assert.equal(result.value,'verified');
  assert.equal(result.recovery_attempts,2);
  assert.deepEqual(attempts,[
    {reason:'ui_changed',attempt:1},
    {reason:'ui_changed',attempt:2},
  ]);
});

test('does not retry identity failures after the bounded budget', async()=>{
  const attempts=[];
  await assert.rejects(
    runWithRecovery(
      async()=>{ throw new Error('account_mismatch'); },
      async(reason,attempt)=>attempts.push({reason,attempt}),
    ),
    error=>error.message==='account_mismatch' && error.recovery_attempts===MAX_RECOVERY_ATTEMPTS,
  );
  assert.equal(attempts.length,MAX_RECOVERY_ATTEMPTS);
});

test('unknown errors are not retried and are classified safely', async()=>{
  let attempts=0;
  await assert.rejects(
    runWithRecovery(async()=>{ throw new Error('bridge_disconnected'); },async()=>{attempts++;}),
    error=>error.message==='bridge_disconnected' && error.recovery_attempts===0,
  );
  assert.equal(attempts,0);
  assert.equal(recoveryReason(new Error('unrecognized')), 'ui_changed');
});
