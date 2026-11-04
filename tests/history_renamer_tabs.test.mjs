import {test} from 'node:test';
import assert from 'node:assert/strict';
import {openOrReuseTab} from '../extensions/history-renamer/tabs.js';

test('creates a new work tab when the remembered tab no longer exists', async () => {
  const calls=[];
  const tabs={
    async get(id){calls.push(['get',id]);throw new Error(`No tab with id: ${id}.`);},
    async update(...args){calls.push(['update',...args]);},
    async create(options){calls.push(['create',options]);return {id:42};},
  };
  assert.equal(await openOrReuseTab(tabs,'https://gemini.google.com/notebook/abc',111),42);
  assert.deepEqual(calls.map(x=>x[0]),['get','create']);
  assert.deepEqual(calls[1][1],{url:'https://gemini.google.com/notebook/abc',active:true});
});

test('recovers when the tab closes between lookup and navigation', async () => {
  const calls=[];
  const tabs={
    async get(id){calls.push(['get',id]);return {id};},
    async update(id,options){calls.push(['update',id,options]);throw new Error(`No tab with id: ${id}.`);},
    async create(options){calls.push(['create',options]);return {id:43};},
  };
  assert.equal(await openOrReuseTab(tabs,'https://gemini.google.com/app',11),43);
  assert.deepEqual(calls.map(x=>x[0]),['get','update','create']);
});

test('reuses a live tab and propagates unrelated browser errors', async () => {
  let created=0;
  const tabs={
    async get(id){return {id};},
    async update(id,options){assert.equal(id,7);assert.equal(options.active,true);},
    async create(){created++;return {id:8};},
  };
  assert.equal(await openOrReuseTab(tabs,'https://gemini.google.com/app',7),7);
  assert.equal(created,0);
  tabs.update=async()=>{throw new Error('Missing host permission');};
  await assert.rejects(openOrReuseTab(tabs,'https://gemini.google.com/app',7),/Missing host permission/);
  assert.equal(created,0);
});
