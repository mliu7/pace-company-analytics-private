const {test} = require('node:test');
const assert = require('node:assert/strict');
const G = require('../../apps/dashboard/static/dashboard/project_map_geometry.js');
const project = ([lng,lat]) => ({x:lng*1000,y:lat*1000});
const site = (id,lng,lat,cv=100) => ({id,lng,lat,cv,q:'exact',src:'site'});

test('dense lakefront groups stay anchored to real coordinates at every scale', () => {
  const sites=Array.from({length:120},(_,i)=>site(String(i),-87.62,41.88,120-i));
  sites.push(site('nearby',-87.621,41.881));
  for(const radius of [0,14,25,38]) {
    const groups=G.groupSites(sites,project,radius);
    assert.equal(groups.flatMap(g=>g.members).length,sites.length);
    for(const group of groups)assert.ok(sites.some(s=>s.lng===group.c[0]&&s.lat===group.c[1]));
    assert.deepEqual(groups[0].c,[-87.62,41.88]);
    assert.equal(groups[0].members.filter(s=>s.id!=='nearby').length,120);
  }
});
test('grouping is deterministic and nearby separate sites return at close zoom', () => {
  const sites=[site('a',-87.62,41.88,500),site('b',-87.621,41.881,300),site('c',-88,42,50)];
  assert.deepEqual(G.groupSites(sites,project,25),G.groupSites(sites.slice().reverse(),project,25));
  assert.equal(G.groupSites(sites,project,25).length,2);
  assert.equal(G.groupSites(sites,project,0).length,3);
});
test('polygon containment respects holes and multipolygons', () => {
  const shape={type:'Polygon',coordinates:[[[0,0],[4,0],[4,4],[0,4],[0,0]],[[1,1],[2,1],[2,2],[1,2],[1,1]]]};
  assert.equal(G.contains([.5,.5],shape),true);
  assert.equal(G.contains([1.5,1.5],shape),false);
  assert.equal(G.contains([5,5],shape),false);
  assert.equal(G.contains([.5,.5],{type:'MultiPolygon',coordinates:[shape.coordinates]}),true);
});
test('building match requires a precise location within 18 meters', () => {
  const p=site('a',-87.62,41.88);
  const building={type:'Feature',properties:{render_height:25},geometry:{type:'Polygon',coordinates:G.square(p.lng,p.lat,20)}};
  assert.equal(G.matchBuilding(p,[building]),building);
  const near=G.offset(p.lng,p.lat,20,0), far=G.offset(p.lng,p.lat,50,0);
  assert.equal(G.matchBuilding({...p,lng:near[0]},[building]),building);
  assert.equal(G.matchBuilding({...p,lng:far[0]},[building]),null);
  assert.equal(G.matchBuilding({...p,q:'city'},[building]),null);
  assert.equal(G.matchBuilding({...p,q:'approx'},[building]),null);
  assert.equal(G.matchBuilding({...p,src:'manual',q:'approx'},[building]),building);
});
test('symbol footprints remain centered and bounded in meters', () => {
  const p=site('x',-87.62,41.88);
  const ring=G.square(p.lng,p.lat,40)[0];
  assert.ok(Math.abs(G.meters(ring[0],ring[1])-40)<.1);
  assert.deepEqual(ring[0],ring[ring.length-1]);
  assert.ok(G.contains([p.lng,p.lat],{type:'Polygon',coordinates:[ring]}));
});

test('individual job campuses fit completely inside the shoreline clearance, even at dense addresses', () => {
  const anchor=[-87.62,41.88];
  const shore=G.offset(...anchor,65,0);
  const water={geometry:{type:'Polygon',coordinates:[[[shore[0],41.7],[-87,41.7],[-87,42],[shore[0],42],[shore[0],41.7]]]}};
  const clearance=G.shoreClearance(anchor,[water],1500);
  assert.ok(Math.abs(clearance-65)<.1);
  for(const n of [1,2,5,18,120]) {
    const members=Array.from({length:n},(_,i)=>site(String(i),...anchor,n-i));
    const group=G.groupSites(members,project,0)[0],radius=clearance*.68;
    const layout=G.campusLayout(group,members.map(()=>1500),radius);
    assert.equal(layout.length,n);
    assert.equal(new Set(layout.map(t=>t.id)).size,n);
    for(const t of layout)for(const corner of G.square(...t.c,t.side)[0]) {
      assert.ok(G.meters(anchor,corner)<=radius+.1,'entire building stays inside bounded campus');
      assert.equal(G.contains(corner,water.geometry),false,'no footprint crosses into water');
    }
    assert.deepEqual(group.c,anchor,'navigation anchor remains the recorded coordinate');
    assert.deepEqual(layout,G.campusLayout(group,members.map(()=>1500),radius));
  }
});
test('campus footprints respect requested job sizes and stay centered for a single site', () => {
  const members=[site('large',-87.7,41.88,1000),site('small',-87.7,41.88,10)];
  const group=G.groupSites(members,project,0)[0];
  const layout=G.campusLayout(group,[100,20],500);
  assert.ok(layout[0].side>layout[1].side);
  assert.deepEqual(G.campusLayout({...group,members:members.slice(0,1)},[100],500)[0].c,group.c);
  assert.equal(G.shoreClearance(group.c,[],500),500);
});

test('building matches isolate a single polygon from batched vector-tile features', () => {
  const p=site('depaul',-87.653497,41.924381),local=G.square(p.lng,p.lat,35);
  const far=G.square(p.lng-.02,p.lat+.015,20);
  const batch={id:42,type:'Feature',properties:{render_height:24},geometry:{type:'MultiPolygon',coordinates:[far,local]}};
  const match=G.matchBuilding(p,[batch]);
  assert.equal(match.geometry.type,'Polygon');
  assert.deepEqual(match.geometry.coordinates,local);
  assert.equal(G.contains([p.lng-.02,p.lat+.015],match.geometry),false);
  assert.equal(G.buildingParts([batch,batch]).length,2,'duplicate vector tiles do not duplicate footprints');
  assert.equal(G.buildingParts([batch],[[p.lng,p.lat]]).length,1,'only nearby components are indexed');
});
test('regional tower dimensions retain screen size when zooming out, and stop shrinking at street zoom', () => {
  const a=G.towerDimensions(100000,500000,9.6),b=G.towerDimensions(100000,500000,7.6);
  assert.equal(b.h,a.h*4);assert.equal(b.side,a.side*4);
  const street=G.towerDimensions(100000,500000,15);
  for(const zoom of [16,18,20,22])assert.deepEqual(G.towerDimensions(100000,500000,zoom),street);
  assert.ok(street.h>=48&&street.side>=36,'street-scale buildings cannot collapse below a single story');
});
test('higher site totals produce larger buildings without a shared upper-size clamp', () => {
  const values=[0,1000,10000,100000,1000000,10000000];
  const sizes=values.map(value=>G.towerDimensions(value,500000,9.6));
  for(let i=1;i<sizes.length;i++){assert.ok(sizes[i].h>sizes[i-1].h);assert.ok(sizes[i].side>sizes[i-1].side);}
  assert.ok(sizes[5].h>sizes[4].h*2,'large jobs remain distinguishable');
});
test('broad regional footprints clip to land without duplicating the tower across a river', () => {
  const clipping=require('../../apps/dashboard/static/dashboard/vendor/polygon-clipping-0.15.7.min.js');
  const anchor=[-87.63,41.88],west=G.offset(...anchor,30,0),east=G.offset(...anchor,55,0);
  const river={geometry:{type:'Polygon',coordinates:[[[west[0],41.87],[east[0],41.87],[east[0],41.89],[west[0],41.89],[west[0],41.87]]]}};
  const footprint=G.landFootprint(anchor,300,[river],clipping);
  assert.ok(G.contains(anchor,footprint));
  assert.ok(G.meters(footprint.coordinates[0][0],footprint.coordinates[0][1])>100,'land parcel retains broad architecture');
  const crossing=clipping.intersection(footprint.coordinates,river.geometry.coordinates);
  assert.deepEqual(crossing,[]);
  assert.ok(G.bounds(footprint)[2]<=west[0]+1e-9,'no duplicate tower on the opposite river bank');
});
test('actual L-shaped footprints and courtyards triangulate without filling their holes', () => {
  const earcut=require('../../apps/dashboard/static/dashboard/vendor/earcut-2.2.4.min.js');
  const rings=[[[0,0],[100,0],[100,40],[50,40],[50,100],[0,100]],[[10,10],[10,30],[30,30],[30,10]]];
  const flat=earcut.flatten(rings),triangles=earcut(flat.vertices,flat.holes,2);
  assert.ok(triangles.length>0);
  assert.ok(earcut.deviation(flat.vertices,flat.holes,2,triangles)<1e-10);
});
