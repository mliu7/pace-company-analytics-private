/* Job-sized architectural towers. MapLibre 4 custom layer; no external renderer or textures. */
(function (root) {
  'use strict';
  const vertex = `
    precision highp float;
    attribute vec3 a_pos;
    attribute vec3 a_normal;
    attribute vec2 a_uv;
    attribute vec4 a_from;
    attribute vec4 a_to;
    attribute vec3 a_color;
    attribute vec4 a_meta;
    uniform mat4 u_matrix;
    uniform float u_progress;
    uniform vec2 u_viewScale;
    uniform float u_selected;
    uniform float u_hovered;
    varying vec3 v_normal;
    varying vec2 v_uv;
    varying vec3 v_color;
    varying vec4 v_detail;
    void main() {
      float t = clamp((u_progress - a_meta.y) / (1.0 - a_meta.y), 0.0, 1.0);
      t = 1.0 - pow(1.0 - t, 3.0);
      vec4 d = mix(a_from, a_to, t);
      vec3 pos = vec3(d.xy + a_pos.xy*d.z, a_pos.z*d.w*u_viewScale.y);
      gl_Position = u_matrix * vec4(pos, 1.0);
      v_normal = a_normal;
      v_uv = a_uv;
      v_color = a_color;
      v_detail = vec4(a_meta.z, a_meta.w, float(abs(a_meta.x-u_selected)<0.1), float(abs(a_meta.x-u_hovered)<0.1));
    }`;
  const fragment = `
    precision highp float;
    uniform float u_night;
    varying vec3 v_normal;
    varying vec2 v_uv;
    varying vec3 v_color;
    varying vec4 v_detail;
    void main() {
      float seed = v_detail.x;
      float floors = v_detail.y;
      float face = max(dot(normalize(v_normal), normalize(vec3(-0.7,-0.9,1.2))), 0.0);
      vec3 glass = mix(vec3(0.065,0.14,0.22), v_color, 0.76);
      vec3 color = glass * (0.64 + face * 0.62);
      vec3 smoothGlass = color;
      if (v_normal.z < 0.5) {
        vec2 grid = vec2(v_uv.x * 9.0, v_uv.y * floors);
        vec2 cell = fract(grid);
        float pane = smoothstep(0.06,0.13,cell.x) * (1.0-smoothstep(0.87,0.94,cell.x));
        pane *= smoothstep(0.12,0.23,cell.y) * (1.0-smoothstep(0.78,0.88,cell.y));
        float reflection = pow(max(0.0, sin(v_uv.x*4.3 + v_uv.y*2.0 + seed*3.0)), 3.0);
        color = mix(color*0.66, color + vec3(0.20,0.28,0.32)*reflection, pane);
        float occupied = step(0.72, fract(sin(dot(floor(grid),vec2(12.9898,78.233))+seed*90.0)*43758.5453));
        color = mix(color, vec3(1.0,0.81,0.43), occupied*pane*(0.10+u_night*0.65));
        #ifdef SMOOTH_FACADES
        // Fade details smaller than a pixel: distant façades read as glass, without shimmering.
        float detail = 1.0-smoothstep(0.3,0.95,max(fwidth(grid.x),fwidth(grid.y)));
        color = mix(smoothGlass + vec3(0.10,0.15,0.18)*reflection, color, detail);
        #endif
        color *= 1.0 + u_night*0.25;
      } else {
        // Broad, level roof decks and a restrained metal finish; no crowns or pointed tiers.
        color = mix(vec3(0.43,0.51,0.57),v_color,0.24)*(0.9+face*0.3);

      }
      color += v_detail.z*vec3(0.19,0.23,0.26) + v_detail.w*vec3(0.14,0.17,0.19);
      gl_FragColor = vec4(color,1.0);
    }`;
  const clamp = t => Math.max(0,Math.min(1,t));
  const ease = t => 1-Math.pow(1-clamp(t),3);
  const hash = id => { let n=0; for(const c of String(id)) n=(n*31+c.charCodeAt(0))>>>0; return (n%1000)/1000; };
  function hull(points) {
    const sorted=points.slice().sort((a,b)=>a.x-b.x||a.y-b.y);
    const cross=(a,b,c)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x), lower=[],upper=[];
    for(const p of sorted){while(lower.length>1&&cross(lower[lower.length-2],lower[lower.length-1],p)<=0)lower.pop();lower.push(p);}
    for(const p of sorted.slice().reverse()){while(upper.length>1&&cross(upper[upper.length-2],upper[upper.length-1],p)<=0)upper.pop();upper.push(p);}
    return lower.slice(0,-1).concat(upper.slice(0,-1));
  }
  function distance(point, polygon) {
    let inside=false,nearest=Infinity;
    for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){
      const a=polygon[j],b=polygon[i],dx=b.x-a.x,dy=b.y-a.y;
      if((a.y>point.y)!==(b.y>point.y)&&point.x<(b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x)inside=!inside;
      const t=clamp(((point.x-a.x)*dx+(point.y-a.y)*dy)/(dx*dx+dy*dy||1));
      nearest=Math.min(nearest,Math.hypot(point.x-a.x-t*dx,point.y-a.y-t*dy));
    }
    return inside?0:nearest;
  }
  class TowerLayer {
    constructor({night=false,reducedMotion=false}={}) {
      this.id='job-skyline';this.type='custom';this.renderingMode='3d';
      this.night=night;this.reducedMotion=reducedMotion;this.items=new Map();this.screen=[];this.selected=null;this.hovered=null;
      this.origin=maplibregl.MercatorCoordinate.fromLngLat([-87.7,41.88]);
      this.start=0;this.ms=900;this.zoom=9.6;this.count=0;this.available=false;
    }
    onAdd(map,gl) {
      this.map=map;this.gl=gl;
      const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){const msg=gl.getShaderInfoLog(shader);gl.deleteShader(shader);throw new Error(msg);}return shader;};
      try {
        this.program=gl.createProgram();
        const webgl2=!!gl.createVertexArray,derivatives=webgl2||gl.getExtension('OES_standard_derivatives');
        const vsSource=webgl2?'#version 300 es\n'+vertex.replace(/attribute /g,'in ').replace(/varying /g,'out '):vertex;
        const fsSource=webgl2?'#version 300 es\n#define SMOOTH_FACADES\n'+fragment.replace(/varying /g,'in ').replace('precision highp float;','precision highp float;\nout vec4 fragColor;').replace('gl_FragColor','fragColor'):
          (derivatives?'#extension GL_OES_standard_derivatives : enable\n#define SMOOTH_FACADES\n':'')+fragment;
        const vs=compile(gl.VERTEX_SHADER,vsSource),fs=compile(gl.FRAGMENT_SHADER,fsSource);
        gl.attachShader(this.program,vs);gl.attachShader(this.program,fs);gl.linkProgram(this.program);gl.deleteShader(vs);gl.deleteShader(fs);
        if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(this.program));
        this.buffer=gl.createBuffer();this.attrs={};this.uniforms={};
        for(const name of ['pos','normal','uv','from','to','color','meta'])this.attrs[name]=gl.getAttribLocation(this.program,'a_'+name);
        for(const name of ['matrix','progress','selected','hovered','night','viewScale'])this.uniforms[name]=gl.getUniformLocation(this.program,'u_'+name);
        // A private VAO keeps our attributes out of MapLibre's own draw state.
        this.vaoAPI=gl.createVertexArray ? {create:()=>gl.createVertexArray(),bind:v=>gl.bindVertexArray(v),remove:v=>gl.deleteVertexArray(v)} : (()=>{const ext=gl.getExtension('OES_vertex_array_object');return ext&&{create:()=>ext.createVertexArrayOES(),bind:v=>ext.bindVertexArrayOES(v),remove:v=>ext.deleteVertexArrayOES(v)};})();
        if(!this.vaoAPI)throw new Error('Vertex arrays unavailable');
        if(!root.earcut)throw new Error('Footprint triangulation unavailable');
        this.vao=this.vaoAPI.create();this.available=true;
      } catch(error) { console.warn('Architectural tower renderer unavailable; using solid 3D towers.',error); }
    }
    progress(now=performance.now()) {return this.reducedMotion?1:clamp((now-this.start)/this.ms);}
    current(item,t) {const k=ease((t-item.delay)/(1-item.delay));return item.from.map((v,i)=>v+(item.to[i]-v)*k);}
    viewScale() {
      // World footprints stay on their parcels. Height magnifies regionally, then stops shrinking at street scale.
      return [1,PCAMapGeometry.towerScale(this.map.getZoom())/PCAMapGeometry.towerScale(this.zoom)];
    }
    scaled(item,d,viewScale) {return [d[0],d[1],d[2],d[3]*viewScale[1]];}
    setData(towers,{selected=null,ms=900}={}) {
      this.selected=selected;
      if(!this.available)return;
      const signature=JSON.stringify(towers.map(t=>[t.id,t.c,t.side,t.h,t.color,t.geometry]));
      if(signature===this.signature){this.map.triggerRepaint();return;}
      this.signature=signature;
      const old=this.items,progress=this.progress(),viewScale=this.viewScale(),next=new Map(),data=[];
      this.ms=ms;this.start=performance.now();this.zoom=this.map.getZoom();
      towers.forEach((tower,index)=>{
        const coord=maplibregl.MercatorCoordinate.fromLngLat(tower.c),units=coord.meterInMercatorCoordinateUnits();
        const to=[coord.x-this.origin.x,coord.y-this.origin.y,tower.side*units,tower.h*units];
        const previous=old.get(tower.id),seed=hash(tower.id);
        let from=[to[0],to[1],to[2],0];
        if(previous)from[3]=this.current(previous,progress)[3]*viewScale[1];
        const item={...tower,index:index+1,seed,delay:previous?0:seed*.16,to,from};
        next.set(tower.id,item);
        const color=tower.color.startsWith('rgb')?tower.color.match(/[\d.]+/g).slice(0,3).map(Number):[1,3,5].map(i=>parseInt(tower.color.slice(i,i+2),16));
        const floors=Math.max(8,Math.min(64,Math.round(tower.streetH/4)));
        const params=[...item.from,...to,...color.map(v=>v/255),item.index,item.delay,seed,floors];
        const add=(pos,normal,uv)=>data.push(...pos,...normal,...uv,...params);
        const geometry=tower.geometry||{type:'Polygon',coordinates:PCAMapGeometry.square(...tower.c,tower.side)};
        const shapes=geometry.type==='MultiPolygon'?geometry.coordinates:[geometry.coordinates];
        item.vertices=[];
        for(const shape of shapes){
          const rings=shape.map(ring=>ring.slice(0,-1).map(c=>{const p=maplibregl.MercatorCoordinate.fromLngLat(c);return [(p.x-coord.x)/(tower.side*units),(p.y-coord.y)/(tower.side*units)];}));
          item.vertices.push(...rings[0]);
          for(let r=0;r<rings.length;r++){
            const ring=rings[r];let area=0;
            for(let i=0;i<ring.length;i++){const a=ring[i],b=ring[(i+1)%ring.length];area+=a[0]*b[1]-b[0]*a[1];}
            const sign=(area>0?1:-1)*(r===0?1:-1);
            for(let i=0;i<ring.length;i++){
              const a=ring[i],b=ring[(i+1)%ring.length],dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
              if(length<1e-8)continue;
              const normal=[sign*dy/length,-sign*dx/length,0];
              const vertices=[[...a,0],[...b,0],[...b,1],[...a,1]],uv=[[0,0],[length,0],[length,1],[0,1]];
              for(const j of [0,1,2,0,2,3])add(vertices[j],normal,uv[j]);
            }
          }
          const flat=earcut.flatten(rings),triangles=earcut(flat.vertices,flat.holes,flat.dimensions);
          for(const i of triangles){const x=flat.vertices[i*2],y=flat.vertices[i*2+1];add([x,y,1],[0,0,1],[x+.5,y+.5]);}
        }

      });
      this.items=next;this.screen=[];this.count=data.length/23;
      this.gl.bindBuffer(this.gl.ARRAY_BUFFER,this.buffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,new Float32Array(data),this.gl.DYNAMIC_DRAW);
      this.map.triggerRepaint();
    }
    highlight(id) {if(this.hovered===id)return;this.hovered=id;this.map.triggerRepaint();}
    render(gl,matrix) {
      if(!this.available||!this.count)return;
      const m=new Float32Array(matrix);
      for(let row=0;row<4;row++)m[12+row]=matrix[12+row]+matrix[row]*this.origin.x+matrix[4+row]*this.origin.y;
      const progress=this.progress(),viewScale=this.viewScale();
      gl.useProgram(this.program);this.vaoAPI.bind(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
      let offset=0;
      for(const [name,size] of [['pos',3],['normal',3],['uv',2],['from',4],['to',4],['color',3],['meta',4]]){
        const loc=this.attrs[name];gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,size,gl.FLOAT,false,92,offset*4);offset+=size;
      }
      gl.uniformMatrix4fv(this.uniforms.matrix,false,m);
      gl.uniform2fv(this.uniforms.viewScale,viewScale);
      gl.uniform1f(this.uniforms.progress,progress);gl.uniform1f(this.uniforms.night,this.night?1:0);
      gl.uniform1f(this.uniforms.selected,this.items.get(this.selected)?.index||-1);gl.uniform1f(this.uniforms.hovered,this.items.get(this.hovered)?.index||-1);
      gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.depthMask(true);gl.disable(gl.CULL_FACE);gl.disable(gl.BLEND);
      gl.drawArrays(gl.TRIANGLES,0,this.count);this.vaoAPI.bind(null);
      this.projectTowers(m,progress,viewScale);
      if(progress<1)this.map.triggerRepaint();
    }
    projectTowers(m,progress,viewScale) {
      const canvas=this.map.getCanvas(),width=canvas.clientWidth,height=canvas.clientHeight;
      const project=(x,y,z)=>{
        const w=m[3]*x+m[7]*y+m[11]*z+m[15];if(w<=0)return null;
        return {x:((m[0]*x+m[4]*y+m[8]*z+m[12])/w+1)*width/2,y:(1-(m[1]*x+m[5]*y+m[9]*z+m[13])/w)*height/2,depth:(m[2]*x+m[6]*y+m[10]*z+m[14])/w};
      };
      this.screen=[];
      for(const t of this.items.values()){
        const [x,y,side,h]=this.scaled(t,this.current(t,progress),viewScale),points=[];
        for(const [dx,dy] of t.vertices)for(const z of [0,1]){const p=project(x+dx*side,y+dy*side,h*z);if(p)points.push(p);}
        if(points.length<6)continue;
        const polygon=hull(points),xs=points.map(p=>p.x),ys=points.map(p=>p.y);
        const bounds={left:Math.min(...xs),right:Math.max(...xs),top:Math.min(...ys),bottom:Math.max(...ys)};
        if(bounds.right< -20||bounds.left>width+20||bounds.bottom< -20||bounds.top>height+20)continue;
        this.screen.push({id:t.id,group:t.group,polygon,bounds,roof:project(x,y,h),depth:Math.min(...points.map(p=>p.depth))});
      }
    }
    hitTest(point,radius=14) {
      const candidates=[];
      for(const t of this.screen){const b=t.bounds;if(point.x<b.left-radius||point.x>b.right+radius||point.y<b.top-radius||point.y>b.bottom+radius)continue;
        const d=distance(point,t.polygon);if(d<=radius)candidates.push({...t,d});}
      candidates.sort((a,b)=>a.d-b.d||a.depth-b.depth||String(a.id).localeCompare(String(b.id)));
      const t=candidates[0];return t?{id:t.group,pid:this.items.get(t.id)?.count===1?t.id:null,score:t.d}:null;
    }
    onRemove(map,gl) {
      if(this.buffer)gl.deleteBuffer(this.buffer);if(this.program)gl.deleteProgram(this.program);if(this.vao)this.vaoAPI.remove(this.vao);
      this.available=false;this.screen=[];
    }
  }
  root.PCATowerLayer=TowerLayer;
})(window);
