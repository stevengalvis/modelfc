// Local live-mode dev server on :3001 with API URL http://127.0.0.1:3999/api/v1.
// Every API call is intercepted. This is synthetic boundary testing, not live verification.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.BROWSER_PATH || '/usr/bin/chromium',args:['--no-sandbox']});
  for (const width of [1440,375]) {
    const page = await browser.newPage({viewport:{width,height:900}});
    let fail = false;
    await page.route('http://127.0.0.1:3999/api/v1/**', route => route.fulfill({status:fail ? 503 : 200, contentType:'application/json', body:fail ? '{}' : '[]'}));
    await page.goto('http://127.0.0.1:3001/research/btts');
    await page.getByText('No BTTS research comparisons yet').waitFor();
    assert.equal(await page.getByRole('navigation').getByRole('link',{name:'Analyze',exact:true}).count(),0);
    assert.equal(await page.locator('nav a').count(),6);
    assert.equal(await page.locator('nav [aria-current="page"]').textContent(),'BTTS Value');
    if (width === 375) await page.screenshot({path:path.join(__dirname,'empty-mobile.png')});
    fail = true;
    await page.reload();
    await page.locator('main [role=alert]').waitFor();
    assert.equal(await page.getByText('No BTTS research comparisons yet').count(),0);
    if (width === 375) await page.screenshot({path:path.join(__dirname,'error-mobile.png')});
    await page.getByRole('navigation').getByRole('link',{name:'Next matches',exact:true}).click();
    await page.getByText('Upcoming coverage unavailable or incomplete').waitFor();
    assert.equal(await page.locator('nav [aria-current="page"]').textContent(),'Next matches');
    await page.goBack();
    await page.waitForURL('**/research/btts');
    assert.equal(await page.locator('nav [aria-current="page"]').textContent(),'BTTS Value');
    await page.close();
    console.log(`PASS ${width}px: six live-mode links, empty/error distinction, navigation during outage, Back`);
  }
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
