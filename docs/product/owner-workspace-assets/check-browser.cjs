const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const base=path.resolve(__dirname,'..');
const output=path.join(base,'owner-workspace-assets');fs.mkdirSync(output,{recursive:true});
(async()=>{const browser=await chromium.launch({headless:true});const results=[];
try{for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900},offline:true});
 const page=await context.newPage(),errors=[],network=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith('file:'))network.push(r.url())});
 await page.goto('file://'+path.join(base,'owner-workspace-wireframe.html'));
 await page.keyboard.press('Tab');assert.equal(await page.locator(':focus').textContent(),'跳到主内容');
 for(const role of ['owner','pm','member','finance']){
  await page.locator('[data-role="'+role+'"]').click();assert(await page.locator('#'+role).isVisible());assert.equal(await page.locator('#metric-contract').evaluate(e=>e.hidden),!['owner','finance'].includes(role));if(width===390){const bad=await page.locator('a:visible,button:visible,summary:visible').evaluateAll(es=>es.filter(e=>{const r=e.getBoundingClientRect();return r.y>=0&&r.x>=0&&(r.height<43.9||r.width<43.9)}).map(e=>e.textContent));assert.deepEqual(bad,[],'44px touch targets')}
  for(const other of ['owner','pm','member','finance'].filter(x=>x!==role))assert(!(await page.locator('#'+other).isVisible()));
  for(const state of ['normal','missing','overdue','denied','mobile']){
   await page.locator('[data-state="'+state+'"]').click();assert(await page.locator('#state-'+state).isVisible());
   assert.equal(await page.locator('#workspace').isVisible(),state!=='denied');if(state==='missing'){const values=await page.locator('[data-value]:visible').allTextContents();if(['owner','finance'].includes(role))assert(values.length>0,'missing scenario must cover monetary fields');assert(values.every(v=>v.trim()==='缺项 / 不可计算'),'missing amount must stay unknown, not zero')}
   if(state==='denied'){const text=await page.locator('body').innerText();assert(!text.includes('500.00 元')&&!text.includes('530.00 元')&&!text.includes('318.00 元'))}
  }
 }
 await page.locator('[data-state="normal"]').click();await page.locator('[data-role="owner"]').click();
 await page.locator('#tab-owner').focus();await page.keyboard.press('End');assert.equal(await page.locator(':focus').getAttribute('id'),'tab-finance');assert(await page.locator('#finance').isVisible());
 await page.keyboard.press('Home');assert.equal(await page.locator(':focus').getAttribute('id'),'tab-owner');
 await page.keyboard.press('ArrowRight');assert.equal(await page.locator(':focus').getAttribute('id'),'tab-pm');assert(await page.locator('#pm').isVisible());
 await page.locator('[data-role="owner"]').click();await page.locator('a[href="#pm-todo"]').first().click();await page.waitForFunction(()=>document.activeElement.id==='pm-todo');assert(await page.locator('#pm').isVisible());assert.equal(await page.locator(':focus').getAttribute('id'),'pm-todo');
 if(width===390){await page.locator('#menu').click();assert(await page.locator('#navigation').isVisible());await page.keyboard.press('Tab');assert(await page.locator('#navigation').evaluate(e=>e.contains(document.activeElement)));await page.keyboard.press('Escape');assert(!(await page.locator('#navigation').isVisible()));assert.equal(await page.locator(':focus').getAttribute('id'),'menu')}
 await page.locator('[data-role="owner"]').click();await page.locator('[data-state="normal"]').click();
 const widths=[];for(const w of [320,360,390,480,640,768,1024,1440]){await page.setViewportSize({width:w,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)),'body overflow width '+w);widths.push(w)}
 await page.setViewportSize({width,height:900});await page.screenshot({path:path.join(output,'browser-'+width+'.png'),fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(network,[]);results.push({width,roles:4,states:5,checks:['state-denial-hides-all-workspace','missing-not-zero','tab-arrows-home-end','cross-role-action-focus',...(width===390?['mobile-dialog-tab-escape-return','44px-touch-targets']:[]),'no-page-errors','offline-no-network'],no_body_overflow_widths:widths});await context.close();
}
const digest=p=>require('crypto').createHash('sha256').update(fs.readFileSync(p)).digest('hex');const proof={input_artifacts:[{path:'owner-workspace-wireframe.html',sha256:digest(path.join(base,'owner-workspace-wireframe.html'))},{path:'../engineering/ui-tokens.css',sha256:digest(path.join(base,'../engineering/ui-tokens.css'))}],artifact_sha256:{'browser-1440.png':digest(path.join(output,'browser-1440.png')),'browser-390.png':digest(path.join(output,'browser-390.png'))},kind:'actual_browser_static_prototype_only',engine:browser.version(),run_at:new Date().toISOString(),product_backend_verified:false,customer_acceptance:false,results};fs.writeFileSync(path.join(output,'browser-evidence.json'),JSON.stringify(proof,null,2)+'\n');console.log(JSON.stringify(proof));
}finally{await browser.close()}})().catch(e=>{console.error(e.stack);process.exitCode=1});
