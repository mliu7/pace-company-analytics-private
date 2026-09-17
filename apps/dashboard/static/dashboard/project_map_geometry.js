/* Pure geographic helpers. Aggregates always use a real site's coordinate, never a displaced centroid. */
(function (root) {
  'use strict';
  const meters = (a, b) => Math.hypot((a[0] - b[0]) * 111320 * Math.cos((a[1] + b[1]) * Math.PI / 360), (a[1] - b[1]) * 111320);
  const offset = (lng, lat, dx, dy) => [lng + dx / (111320 * Math.cos(lat * Math.PI / 180)), lat + dy / 111320];
  function square(lng, lat, side) {
    const a = offset(lng, lat, -side / 2, -side / 2), b = offset(lng, lat, side / 2, side / 2);
    return [[a, [b[0], a[1]], b, [a[0], b[1]], a]];
  }
  function ringContains(p, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
  }
  function polygons(geometry) { return geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : []; }
  function contains(p, geometry) { return polygons(geometry).some(rings => ringContains(p, rings[0]) && !rings.slice(1).some(r => ringContains(p, r))); }
  function distanceToGeometry(p, geometry) {
    if (contains(p, geometry)) return 0;
    let nearest = Infinity;
    for (const rings of polygons(geometry)) for (const ring of rings) for (let i = 1; i < ring.length; i++) {
      const a = ring[i - 1], b = ring[i], cos = Math.cos(p[1] * Math.PI / 180);
      const ax = (a[0] - p[0]) * cos, ay = a[1] - p[1], dx = (b[0] - a[0]) * cos, dy = b[1] - a[1];
      const t = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy || 1)));
      nearest = Math.min(nearest, Math.hypot(ax + t * dx, ay + t * dy) * 111320);
    }
    return nearest;
  }
  function bounds(geometry) {
    const b=[Infinity,Infinity,-Infinity,-Infinity];
    for(const rings of polygons(geometry))for(const ring of rings)for(const [x,y] of ring){b[0]=Math.min(b[0],x);b[1]=Math.min(b[1],y);b[2]=Math.max(b[2],x);b[3]=Math.max(b[3],y);}
    return b;
  }
  const overlaps=(a,b)=>a[0]<=b[2]&&a[2]>=b[0]&&a[1]<=b[3]&&a[3]>=b[1];
  // OpenFreeMap batches many unrelated buildings into one MultiPolygon feature by height.
  // Matching/feature-state on the whole feature would highlight an entire neighborhood.
  function buildingParts(features, points) {
    const unique=new Map();
    for(const f of features){
      if(!f.geometry||f.properties?.hide_3d===true)continue;
      for(const coordinates of polygons(f.geometry)){
        const geometry={type:'Polygon',coordinates},bbox=bounds(geometry);
        if(points&&!points.some(p=>meters(p,[Math.max(bbox[0],Math.min(bbox[2],p[0])),Math.max(bbox[1],Math.min(bbox[3],p[1]))])<20))continue;
        const height=Math.max(6,Number(f.properties?.render_height)||6);
        const key=bbox.map(v=>v.toFixed(7)).join(':')+':'+height+':'+coordinates[0].length;
        if(!unique.has(key))unique.set(key,{type:'Feature',id:key,geometry,bbox,properties:{...f.properties,render_height:height}});
      }
    }
    return [...unique.values()];
  }
  function matchBuilding(site, features) {
    if ((site.q !== 'exact' && site.src !== 'manual') || site.q === 'city') return null;
    const point=[site.lng,site.lat];let best=null,distance=18;
    for(const original of features){
      if(!original.geometry||original.properties?.hide_3d||original.properties?.render_height===0)continue;
      const parts=original.geometry.type==='MultiPolygon'?buildingParts([original]):[original];
      for(const f of parts){
        const b=f.bbox||bounds(f.geometry),nearest=[Math.max(b[0],Math.min(b[2],point[0])),Math.max(b[1],Math.min(b[3],point[1]))];
        if(meters(point,nearest)>distance+.1)continue;
        const d=distanceToGeometry(point,f.geometry);
        if(d<distance || d===distance && best && Number(f.properties?.render_height)>Number(best.properties?.render_height)){best=f;distance=d;}
      }
    }
    return best;
  }
  // Street-scale dimensions have a floor; they must not halve away at zooms 17–22.
  const towerScale=zoom=>Math.max(1,Math.pow(2,13.6-zoom));
  function towerDimensions(value, reference, zoom) {
    const ratio=Math.pow(Math.max(0,value)/Math.max(1,reference),.65),scale=towerScale(zoom);
    return {ratio,side:(36+58*Math.sqrt(ratio))*scale,h:(48+270*ratio)*scale};
  }
  function landFootprint(point,side,water,clipper) {
    const geometry={type:'Polygon',coordinates:square(...point,side)},bbox=bounds(geometry);
    const clips=water.filter(f=>overlaps(bbox,f.bbox||bounds(f.geometry)));
    if(!clips.length)return geometry;
    if(!clipper){const safe=Math.max(1,Math.min(side,shoreClearance(point,clips,side)*1.35));return {type:'Polygon',coordinates:square(...point,safe)};}
    try{
      const land=clipper.difference(geometry.coordinates,...clips.map(f=>f.geometry.coordinates));
      // Keep the connected land parcel containing the location; never create a second tower across a river.
      const containing=land.find(coordinates=>contains(point,{type:'Polygon',coordinates}));
      if(containing)return {type:'Polygon',coordinates:containing};
    }catch(error){ /* A malformed tile ring should not make the map unusable. */ }
    const safe=Math.max(1,Math.min(side,shoreClearance(point,clips,side)*1.35));
    return {type:'Polygon',coordinates:square(...point,safe)};
  }
  function groupSites(sites, project, radius) {
    const sorted = sites.slice().sort((a, b) => (b.cv || 0) - (a.cv || 0) || String(a.id).localeCompare(String(b.id)));
    const groups = [], exact = new Map(), cells = new Map(), size = Math.max(radius, 1);
    for (const site of sorted) {
      const key = site.lat.toFixed(5) + ',' + site.lng.toFixed(5), xy = project([site.lng, site.lat]);
      let group = exact.get(key);
      const cx = Math.floor(xy.x / size), cy = Math.floor(xy.y / size);
      if (!group && radius > 0) {
        let nearest = radius;
        for (let x = cx - 1; x <= cx + 1; x++) for (let y = cy - 1; y <= cy + 1; y++) for (const g of cells.get(x + ':' + y) || []) {
          const distance = Math.hypot(g.xy.x - xy.x, g.xy.y - xy.y);
          if (distance < nearest) { group = g; nearest = distance; }
        }
      }
      if (!group) {
        group = {id:site.id, c:[site.lng, site.lat], xy, members:[]}; groups.push(group);
        const cell = cx + ':' + cy; if (!cells.has(cell)) cells.set(cell, []); cells.get(cell).push(group);
      }
      group.members.push(site); exact.set(key, group);
    }
    return groups;
  }
  // Keep a shared-address campus compact. Its entire footprint fits within the nearest shore,
  // while the source coordinate remains the navigation / provenance anchor.
  function campusLayout(group, desiredSides, radius) {
    const n=group.members.length, cols=Math.ceil(Math.sqrt(n)), cells=[];
    for(let y=0;y<cols;y++)for(let x=0;x<cols;x++)cells.push({x:x-(cols-1)/2,y:y-(cols-1)/2});
    cells.sort((a,b)=>Math.hypot(a.x,a.y)-Math.hypot(b.x,b.y)||a.y-b.y||a.x-b.x);
    const largest=Math.max(...desiredSides,1), cell=Math.min(largest*1.3,radius*2/(Math.SQRT2*cols));
    return group.members.map((p,i)=>{
      const side=Math.max(.01,Math.min(desiredSides[i],cell*.8));
      const c=offset(group.c[0],group.c[1],cells[i].x*cell,cells[i].y*cell);
      return {id:p.id,c,side};
    });
  }
  function shoreClearance(point, water, limit) {
    let distance=limit;
    for(const f of water){
      if(!f.geometry)continue;
      // Bounding boxes skip most vector-tile rings before the more expensive segment scan.
      if(f.bbox){const b=f.bbox, near=[Math.max(b[0],Math.min(b[2],point[0])),Math.max(b[1],Math.min(b[3],point[1]))];if(meters(point,near)>distance)continue;}
      distance=Math.min(distance,distanceToGeometry(point,f.geometry));
    }
    return distance;
  }
  const api = {meters, offset, square, contains, distanceToGeometry, matchBuilding, groupSites, campusLayout, shoreClearance, bounds, overlaps, buildingParts, towerScale, towerDimensions, landFootprint};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PCAMapGeometry = api;
})(typeof window !== 'undefined' ? window : globalThis);
