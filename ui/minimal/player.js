import {time} from './api.js';
// Media clocks quantize seeks to microseconds; frame boundaries can be fractional.
const SEEK_CLOCK_TOLERANCE=1e-6;

// Mapping interpolates the backend's keeps. No cut compilation occurs in the browser.
export class Player {
  constructor(video,seek,clock){this.video=video;this.seek=seek;this.clock=clock;this.timeline=null;this.virtual=false;this.contextEnd=null;this.previousVirtual=false;this.raf=null;
    this.returnTimeline=null;
    seek.addEventListener('input',()=>{video.currentTime=this.toSource(Number(seek.value));});
    video.addEventListener('play',()=>this.schedule());
    video.addEventListener('pause',()=>{cancelAnimationFrame(this.raf);this.raf=null;this.update();});
    video.addEventListener('timeupdate',()=>this.update());
    video.addEventListener('seeking',()=>this.update());
  }
  setTimeline(timeline){if(this.returnTimeline){this.restoreAudition();this.contextEnd=null;}this.timeline=timeline;this.seek.max=timeline.duration;this.seek.disabled=!timeline.keeps.length;this.update();}
  toOutput(source){if(!this.timeline)return source;for(const k of this.timeline.keeps){if(source<k.source_start)return k.output_start;if(source<=k.source_end)return k.output_start+source-k.source_start;}return this.timeline.duration;}
  toSource(output){if(!this.timeline)return output;for(const k of this.timeline.keeps){if(output<k.output_end)return k.source_start+output-k.output_start;}return this.timeline.keeps.at(-1)?.source_end||0;}
  restoreAudition(){if(this.returnTimeline){this.timeline=this.returnTimeline;this.returnTimeline=null;this.seek.max=this.timeline.duration;this.virtual=this.previousVirtual;}}
  setVirtual(enabled){this.restoreAudition();this.virtual=enabled;this.contextEnd=null;this.update();if(!this.video.paused)this.schedule();}
  schedule(){if(this.raf)return;this.raf=requestAnimationFrame(()=>{this.raf=null;this.update();if(!this.video.paused)this.schedule();});}
  update(){const video=this.video;let source=video.currentTime;
    if(this.contextEnd!==null&&!video.seeking&&source>=this.contextEnd){this.contextEnd=null;this.restoreAudition();this.virtual=this.previousVirtual;video.pause();}
    if(this.virtual&&this.timeline&&!video.seeking){const keep=this.timeline.keeps.find(k=>source+SEEK_CLOCK_TOLERANCE>=k.source_start&&source<k.source_end);if(!keep){const next=this.timeline.keeps.find(k=>k.source_start>source);if(next){video.currentTime=next.source_start;source=next.source_start;}else if(!video.paused)video.pause();}}
    this.frame(source);
    const output=this.toOutput(source);this.seek.value=output;this.clock.textContent=`${this.virtual?'Virtual':'Original'} ${time(this.virtual?output:source)} · raw ${time(source)}`;
  }
  // Same punch-in as the export: scale around eye height (40%), clipped by the container.
  frame(source){if(!this.video.style)return;const keep=this.virtual&&this.timeline?this.timeline.keeps.find(k=>source+SEEK_CLOCK_TOLERANCE>=k.source_start&&source<k.source_end):null;
    const scale=keep?.scale||1;const value=scale>1?`scale(${scale})`:'';
    if(this.video.style.transform!==value){this.video.style.transformOrigin='50% 40%';this.video.style.transform=value;if(this.video.parentElement)this.video.parentElement.style.overflow='hidden';}}
  async context(start,end){this.restoreAudition();this.previousVirtual=this.virtual;this.virtual=false;this.contextEnd=Math.min(this.video.duration||end+2,end+2);this.video.currentTime=Math.max(0,start-2);await this.video.play();}
  async audition(timeline,start,end){this.restoreAudition();this.previousVirtual=this.virtual;this.returnTimeline=this.timeline;this.timeline=timeline;this.seek.max=timeline.duration;this.virtual=true;this.contextEnd=Math.min(this.video.duration||end+2,end+2);this.video.currentTime=Math.max(0,start-2);await this.video.play();}
}
