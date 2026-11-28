/* Real Chromium benchmark. Install Playwright outside the app, then run:
 * NODE_PATH=/tmp/folio-mobile-benchmark/node_modules node tests/measure_mobile_startup.cjs
 * Run in an interactive terminal. Send JSON lines: {"action":"measure","label":"before","samples":3},
 * {"action":"measure","label":"after","samples":3}, {"action":"finish"}.
 * Keep this process alive across app edits: same URL, login, data and browser.
 * No temporary app URL, password, cookie or account identifier is saved.
 */
const {chromium} = require('playwright');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const {randomBytes} = require('node:crypto');
const readline = require('node:readline');
const input = readline.createInterface({input: process.stdin});
const email = `mobile-benchmark-${randomBytes(6).toString('hex')}@example.invalid`;
const password = randomBytes(24).toString('hex');
let appUrl, browser, context, page, fixture;
const reportPath = 'tests/mobile_startup_measurements.json';
const report = {
  settings: {viewport: {width:393, height:851}, device_scale_factor:3,
    mobile:true, touch:true, latency_ms:150, download_bits_per_second:1600000,
    upload_bits_per_second:750000, cpu_slowdown:4},
  scope: 'Headless Chromium in the agent environment, simulated mobile connection and CPU. Not a measurement on an actual iPhone or the production host. Synthetic saved portfolio; no external market fetches.',
  milestones: {portfolio_frame:'Paint opportunity after the portfolio Form attaches (framework opening screen cleared).',
    values_frame:'Paint opportunity after saved portfolio totals and holdings are applied.',
    ready_frame:'Paint opportunity after deferred history is available.',
    first_contentful_paint:'Browser FCP can be the framework loading screen, not the app.'},
  runs: []
};
const sanitize = value => String(value).replaceAll(password,'[password]').replaceAll(email,'[fixture]')
  .replace(/[\w.+-]+@[\w.-]+/g,'[email]')
  .replace(/https?:\/\/[^\s"'<>]+/g,'[URL]');
function cli(args, stdin) {
  let result=spawnSync('anvil',args,{input:stdin,encoding:'utf8',timeout:90000,maxBuffer:8*1024*1024});
  // REPL workers can disconnect while an earlier worker is being released.
  // Fixture operations are idempotent; this is outside measured browser runs.
  if(args[0]==='repl' && result.status!==0 && /RuntimeUnavailableError/.test(result.stderr)) {
    console.log('Retrying unavailable temporary REPL worker.');
    result=spawnSync('anvil',args,{input:stdin,encoding:'utf8',timeout:90000,maxBuffer:8*1024*1024});
  }
  if(result.status!==0) throw new Error(sanitize(result.stderr || result.stdout || result.error));
  return result.stdout;
}
function fixtureAction(action) {
  const code=`benchmark_email=${JSON.stringify(email)}\nbenchmark_password=${JSON.stringify(password)}\nbenchmark_action=${JSON.stringify(action)}\n`+
    fs.readFileSync('tests/mobile_startup_fixture.py','utf8');
  const output=cli(['repl','-'],code);
  const match=output.match(/FIXTURE (\{[^\n]+\})/);
  if(match) fixture=JSON.parse(match[1]);
  console.log(action==='create'?'Temporary benchmark portfolio ready.':sanitize(output));
}
function save() { fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n'); }
async function diagnostics() {
  const errorLink=page.getByText('Click for more information',{exact:true});
  if(await errorLink.isVisible()) await errorLink.click();
  return await page.evaluate(() => ({
    marks:Object.fromEntries(performance.getEntriesByType('mark').filter(x=>x.name.startsWith('folio:')).map(x=>[x.name.slice(6),Math.round(x.startTime)])),
    body:document.body.innerText.slice(-4500),
    holdings:document.querySelectorAll('.portfolio-holdings-table-row').length
  }));
}
async function navigate() {
  await page.goto(appUrl,{waitUntil:'domcontentloaded',timeout:90000});
}
async function login() {
  await navigate();
  await page.waitForFunction(()=>window.folioStartupTimings,null,{timeout:60000});
  await page.getByText('Account & plan',{exact:true}).click();
  await page.getByText('Sign in / Create account',{exact:true}).click();
  for(let attempt=0;attempt<2;attempt++) {
    await page.getByPlaceholder('Username or email').fill(email);
    await page.locator('.username-setup input[title="Password"]').fill(password);
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    try {
      await page.locator('.username-setup').waitFor({state:'hidden',timeout:15000});
      console.log('Signed in with remembered session.');
      return;
    } catch(error) {
      if(attempt===1) throw error;
      console.log('Retrying test login after unavailable temporary server worker.');
    }
  }
}
async function measure(label, samples) {
  const cdp=await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:150,
    downloadThroughput:1600000/8,uploadThroughput:750000/8,connectionType:'cellular4g'});
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  for(let sample=1;sample<=samples;sample++) {
    for(const cache of ['cold','warm']) {
      if(cache==='cold') await cdp.send('Network.clearBrowserCache');
      console.log(`Measuring ${label} ${cache} sample ${sample}...`);
      await navigate();
      // Read marks directly as well as the complete report: early values must
      // remain measurable if a later history request or UI action fails.
      try {
        await page.waitForFunction(()=>performance.getEntriesByName('folio:values_frame').length>0,null,{timeout:60000});
      } catch(error) {
        console.log('STARTUP_DIAGNOSTIC '+sanitize(JSON.stringify(await diagnostics())));
        throw error;
      }
      const data=await page.evaluate(()=>({
        marks:Object.fromEntries(performance.getEntriesByType('mark').filter(x=>x.name.startsWith('folio:')).map(x=>[x.name.slice(6),Math.round(x.startTime*10)/10])),
        paints:Object.fromEntries(performance.getEntriesByType('paint').map(x=>[x.name,Math.round(x.startTime*10)/10])),
        document:{response_end_ms:performance.getEntriesByType('navigation')[0].responseEnd,
          transfer_bytes:performance.getEntriesByType('navigation')[0].transferSize},
        summary:document.querySelector('.anvil-role-portfolio-summary-value')?.textContent.trim(),
        holdings:document.querySelectorAll('.portfolio-holdings-table-row').length,
        resources:performance.getEntriesByType('resource').map(x=>({name:new URL(x.name).pathname.split('/').pop(),
          start_ms:Math.round(x.startTime),duration_ms:Math.round(x.duration),transfer_bytes:x.transferSize,
          encoded_bytes:x.encodedBodySize,decoded_bytes:x.decodedBodySize}))
      }));
      if(data.holdings!==8 || !data.marks.values_frame) throw new Error('Saved portfolio was not restored: '+sanitize(JSON.stringify(await diagnostics())));
      report.runs.push({label,cache,sample,revision:spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).stdout.trim(),...data});
      save();
      console.log(JSON.stringify({label,cache,sample,marks:data.marks,paints:data.paints,summary:data.summary,holdings:data.holdings}));
    }
  }
  await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
  await cdp.detach();
}
async function main() {
  const lines=cli(['--json','env','url']).split('\n').filter(Boolean).map(x=>JSON.parse(x));
  appUrl=lines.find(x=>x.data?.url)?.data.url;
  if(!appUrl) throw new Error('No environment URL returned');
  fixtureAction('create');
  report.fixture=fixture;
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  report.browser=browser.version();
  context=await browser.newContext({viewport:{width:393,height:851},deviceScaleFactor:3,isMobile:true,hasTouch:true});
  page=await context.newPage();
  page.setDefaultTimeout(30000);
  page.on('pageerror',e=>console.log('PAGE_ERROR '+sanitize(e.message)));
  page.on('console',msg=>{if(msg.type()==='error')console.log('CONSOLE_ERROR '+sanitize(msg.text()).slice(0,1200));});
  await login();
  console.log('READY: send a JSON measure command.');
  for await(const line of input) {
    if(!line.trim()) continue;
    const command=JSON.parse(line);
    if(command.action==='finish') break;
    if(command.action==='measure') await measure(command.label,command.samples || 3);
    if(command.action==='inspect') console.log('INSPECT '+sanitize(JSON.stringify(await diagnostics())));
    if(command.action==='eval') console.log('EVAL '+sanitize(JSON.stringify(await page.evaluate(command.expression))));
    console.log('READY: send next command.');
  }
}
main().catch(async error=>{
  console.error('BENCHMARK_ERROR '+sanitize(error.message));
  if(page&&!page.isClosed()) console.log('FINAL_DIAGNOSTIC '+sanitize(JSON.stringify(await diagnostics())));
  process.exitCode=1;
}).finally(async()=>{
  input.close();
  if(browser) await browser.close();
  if(appUrl) fixtureAction('cleanup');
});
