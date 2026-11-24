// Runs immutable API source in isolation; no API repository or database writes.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { stripTypeScriptTypes } from 'node:module';
const root = process.cwd(), reference = path.join(root, '.cache/osp-api-reference');
const members = JSON.parse(fs.readFileSync('data/members.json'));
const details = new Map(members.map(m => [m.id, JSON.parse(fs.readFileSync('data/details/'+m.id+'.json'))]));
const state = { unavailable: false, missing: false, empty: false, unauthorized: false };
function source(p) {
  return stripTypeScriptTypes(fs.readFileSync(path.join(reference, p), 'utf8'), {mode:'transform'})
    .replace(/^import[\s\S]*?;\s*$/gm, '').replace(/^export\s*\{[^}]*\};?\s*$/gm, '')
    .replace(/\bexport\s+(?=(?:async\s+)?(?:function|const|let|class))/g, '');
}
class Response {
  constructor(body, options) { this.body=body; this.status=options.status; this.headers=options.headers; }
  static json(body, options) { return new Response(body, options); }
}
function context(route) {
 const sandbox = { console, URL, Date, Set, Map, Math, Number, String, Array, Object, Promise,
  process:{env:{}}, NextResponse:Response,
  validateApiKey:async()=>state.unauthorized?{valid:false,error:'Unauthorized',status:401}:{valid:true},
  fetch:async(url)=>{
    if(state.unavailable) throw new Error('offline test');
    const relative = new URL(url).pathname.split('/data/')[1];
    if(relative?.startsWith('details/')){
      const bid=relative.split('/')[1].slice(0,-5);
      const value=state.missing?undefined:state.empty?{}:details.get(bid);
      return {ok:!!value,status:value?200:404,json:async()=>value};
    }
    return {ok:true,status:200,json:async()=>JSON.parse(fs.readFileSync(path.join(root,'data',relative)))};
  }};
 vm.createContext(sandbox);
 const helpers=['lib/congress.ts','lib/scoring.ts','lib/response.ts','lib/handlers.ts','lib/aggregate.ts'];
 vm.runInContext(helpers.map(source).join('\n')+'\n'+(route?source(route):'')+
   '\nthis.api={computeScore,hasVerifiedSecSignals'+(route?',GET,options':'')+'};',sandbox);
 return sandbox.api;
}
const api=context();
const differences=[];
for(const m of members){
 const result=api.computeScore(m,details.get(m.id));
 assert.deepEqual(Object.keys(result).sort(),['components','drift','flags','stored','total']);
 assert.equal(result.components.wealth_gap.score,0);
 assert.equal(result.components.wealth_gap.max,25);
 assert.equal(typeof result.total,'number');
 for(const [name,part] of Object.entries(result.components)){
   assert.deepEqual(Object.keys(part).sort(),['detail','max','score']);
   assert.equal(typeof part.detail,'string');assert.equal(typeof part.score,'number');
   if(part.score!==m.score_components[name]) differences.push({member:m.id,component:name,site:m.score_components[name],api:part.score});
 }
 assert.equal(api.computeScore({...m,total_raised:1e12,pac_contributions:1e12},null).components.wealth_gap.score,0);
 assert.equal(api.computeScore({...m,edgar_identity_status:'unverified',corporate_insider_signals:500,flags:['trade']},null).flags.includes('trade'),false);
}
const manifest=JSON.parse(fs.readFileSync('validation/api_reference_manifest.json'));
const routes=manifest.files.filter(x=>x.path.includes('/route.ts')).map(x=>x.path);
let calls=0;
for(const route of routes){
 const {GET,options}=context(route);
 const request={nextUrl:new URL('https://offline.test/api?page=1&per_page=5')};
 const ctx={params:Promise.resolve({bioguide_id:'M001169'})};
 const actual=await GET(request,ctx); calls++;
 assert.equal(actual.status,200,route);
 assert.ok('data' in actual.body,route);
 assert.equal(options().status,204);
 if(route.includes('[bioguide_id]')){
   state.missing=true;
   const missing=await GET(request,ctx);calls++;
   // The score route can compute from the summary without a detail file.
   assert.equal(missing.status,route.includes('/score/')?200:404,route);
   state.missing=false;state.empty=true;
   const empty=await GET(request,ctx);calls++;assert.equal(empty.status,200,route);
   if(actual.body.meta)assert.equal(empty.body.meta.total,0,route);
   state.empty=false;
 }
 state.unavailable=true;
 // A fresh context prevents the API's ordinary module cache hiding the failure.
 assert.equal((await context(route).GET(request,ctx)).status,502,route);calls++;
 state.unavailable=false;
 if(!route.includes('/stats/')&&!route.endsWith('/members/route.ts')){
   state.unauthorized=true; assert.equal((await GET(request,ctx)).status,401,route);calls++;state.unauthorized=false;
 }
}
const positive={edgar_identity_status:'verified',edgar_cik:'0000000001',edgar_identity_cik:'0000000001',
 edgar_identity_evidence:['https://www.sec.gov/evidence'],edgar_data_status:'available',edgar_status:'verified_manual',
 corporate_insider_signals:6};
assert.equal(api.hasVerifiedSecSignals(positive),true);
const receiptGap=api.hasVerifiedSecSignals({...positive,edgar_identity_evidence_checks:[{status:'unavailable'}]});
const report={reference_sha:manifest.commit,members:members.length,component_comparisons:members.length*6,
 route_count:routes.length,route_calls:calls,route_contract_checks:'passed',snapshot_score_differences:differences,
 reference_accepts_unretrieved_sec_evidence:receiptGap,
 readiness:differences.length||receiptGap?'blocked_api_alignment':'passed',
 note:'Actual immutable API functions and routes; auth and HTTP transport stubbed. No production API, Neon, or repository writes.'};
fs.writeFileSync('benchmarks/nov24-api-contract.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));

if (process.argv.includes('--enforce-parity') && report.readiness !== 'passed') process.exitCode=1;
