"""Player mapping and contextual playback executed in Node with DOM event fakes.

These verify JS behavior, not browser codecs, rendering or user-perceived fluency.
"""
import json
import shutil
import subprocess
from pathlib import Path
import pytest
from video_editor.timeline import build_timeline


@pytest.mark.skipif(not shutil.which('node'),reason='Node requerido para contrato JS')
def test_player_consumes_backend_timeline_and_context_can_play_removed_range(tmp_path):
    ui = Path(__file__).resolve().parents[1] / 'ui/minimal'
    for name in ('player.js', 'api.js'):
        (tmp_path/name).write_text((ui/name).read_text(encoding='utf-8'),encoding='utf-8')
    (tmp_path/'package.json').write_text('{"type":"module"}',encoding='utf-8')
    timeline=build_timeline(20,[dict(start=3,end=8)])
    script='''
import assert from 'node:assert/strict';
import {Player} from './player.js';
const handlers={};
const video={currentTime:0,duration:20,paused:true,seeking:false,
 addEventListener:(event,fn)=>handlers[event]=fn,pause(){this.paused=true;},
 async play(){this.paused=false;}};
const seek={addEventListener(){}};
const clock={textContent:''};
const player=new Player(video,seek,clock);
player.setTimeline(TIMELINE);
assert.equal(player.toSource(3),8);
assert.equal(player.toOutput(10),5);
player.setVirtual(true);video.currentTime=5;player.update();assert.equal(video.currentTime,8);
await player.context(4,6);assert.equal(player.virtual,false);assert.equal(video.currentTime,2);
video.currentTime=5;player.update();assert.equal(video.currentTime,5);
video.currentTime=9;player.update();assert.equal(video.paused,true);assert.equal(player.virtual,true);
console.log('PLAYER_CONTRACT_OK');
'''.replace('TIMELINE',json.dumps(timeline))
    path=tmp_path/'test.mjs';path.write_text(script,encoding='utf-8')
    result=subprocess.run([shutil.which('node'),str(path)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'PLAYER_CONTRACT_OK' in result.stdout


@pytest.mark.skipif(not shutil.which('node'), reason='Node requerido para contrato JS')
def test_rough_cut_does_not_seek_forever_at_quantized_frame_boundary(tmp_path):
    ui = Path(__file__).resolve().parents[1] / 'ui/minimal'
    for name in ('player.js', 'api.js'):
        (tmp_path / name).write_text((ui / name).read_text(encoding='utf-8'), encoding='utf-8')
    (tmp_path / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
    timeline = build_timeline(1171.47, [dict(start=45.22, end=48.32)], fps=60)
    script = '''
import assert from 'node:assert/strict';
import {Player} from './player.js';
let source=45.22,seeks=0;
const video={duration:1171.47,paused:false,seeking:false,
 get currentTime(){return source;},
 set currentTime(value){source=Math.floor(value*1e6)/1e6;seeks++;},
 addEventListener(){},pause(){this.paused=true;}};
const player=new Player(video,{addEventListener(){}},{textContent:''});
player.timeline=TIMELINE;player.virtual=true;
for(let i=0;i<5;i++)player.update();
assert.equal(seeks,1,'Same frame must not cause repeated seeks');
assert.equal(video.paused,false);
source+=1/60;
player.update();
assert.equal(seeks,1,'Playback must advance beyond the cut');
source=1171.47;
player.update();
assert.equal(video.paused,true,'The real end of the timeline still stops playback');
'''.replace('TIMELINE', json.dumps(timeline))
    path = tmp_path / 'quantized.mjs'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run([shutil.which('node'), str(path)], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which('node'),reason='Node requerido para contrato JS')
def test_audition_restores_real_timeline_and_never_changes_the_edit(tmp_path):
    ui = Path(__file__).resolve().parents[1] / 'ui/minimal'
    for name in ('player.js', 'api.js'):
        (tmp_path/name).write_text((ui/name).read_text(encoding='utf-8'),encoding='utf-8')
    (tmp_path/'package.json').write_text('{"type":"module"}',encoding='utf-8')
    actual=build_timeline(20,[dict(start=3,end=8)])
    trial=build_timeline(20,[dict(start=3,end=8),dict(start=10,end=12)])
    script='''
import assert from 'node:assert/strict';
import {Player} from './player.js';
const video={currentTime:0,duration:20,paused:true,seeking:false,addEventListener(){},
 async play(){this.paused=false;},pause(){this.paused=true;}};
const player=new Player(video,{addEventListener(){}},{textContent:''});
const real=REAL,trial=TRIAL;
player.setTimeline(real);player.setVirtual(true);
await player.audition(trial,10,12);
video.currentTime=11;player.update();assert.equal(video.currentTime,12);
video.currentTime=14;player.update();assert.equal(video.paused,true);
assert.equal(player.timeline,real);assert.equal(player.virtual,true);
assert.equal(real.duration,15,'The real project must not acquire the audition cut');
await player.audition(trial,10,12);player.setTimeline(real);
assert.equal(player.returnTimeline,null);assert.equal(player.contextEnd,null);
'''.replace('REAL',json.dumps(actual)).replace('TRIAL',json.dumps(trial))
    path=tmp_path/'audition.mjs';path.write_text(script,encoding='utf-8')
    result=subprocess.run([shutil.which('node'),str(path)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
