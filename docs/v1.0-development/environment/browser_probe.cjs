// Browser availability only; this is not a product UI or prototype acceptance.
const {chromium} = require('playwright');
(async () => {
 const browser=await chromium.launch({headless:true});
 try {
  const context=await browser.newContext({viewport:{width:390,height:844},offline:true});
  const page=await context.newPage();
  await page.setContent('<!doctype html><html lang="zh"><meta charset="utf-8"><title>合成环境演练</title><button>演练按钮</button></html>');
  await page.keyboard.press('Tab');
  const result=await page.evaluate(()=>({focused:document.activeElement.tagName,viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth}));
  if(result.focused!=='BUTTON'||result.overflow||result.viewport!==390)throw Error('browser_probe_failed');
  console.log(JSON.stringify({result:'pass',kind:'synthetic_browser_availability_only',engine:browser.version(),...result}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
