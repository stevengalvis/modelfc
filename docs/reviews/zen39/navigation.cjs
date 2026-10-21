// Run against a local mock-mode production build with Playwright available.
// BROWSER_PATH can select a locally installed Chromium; no production API is used.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const routes = [['Next matches','/upcoming'],['Recommendations','/recommendations'],['Predictions','/predictions'],['Performance','/performance'],['Teams','/teams'],['BTTS Value','/research/btts']];
(async () => {
  const browser = await chromium.launch({executablePath: process.env.BROWSER_PATH || '/usr/bin/chromium', args:['--no-sandbox']});
  for (const width of [1440, 1024, 375, 320]) {
    const page = await browser.newPage({viewport:{width,height:900}});
    // Align the browser clock with the repository's fixed 2099 upcoming fixture.
    await page.clock.setFixedTime(new Date('2099-10-01T12:00:00Z'));
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:3000/upcoming');
    await page.getByRole('article').first().waitFor();
    for (const [label, route] of routes) {
      const nav = page.getByRole('navigation', {name:'Primary navigation'});
      await nav.getByRole('link', {name:label,exact:true}).click();
      await page.waitForURL(`**${route}`);
      assert.equal(await nav.locator('[aria-current="page"]').textContent(),label);
      assert.equal(await nav.locator('[aria-current="page"]').count(),1);
      // At 320px the unchanged Recommendations heading already overflows; verify the header independently.
      if (!(width === 320 && route === '/recommendations')) assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `overflow at ${width} ${route}`);
      for (const link of await nav.getByRole('link').all()) {
        const box = await link.boundingBox();
        assert(box && box.height >= 44 && box.x >= 0 && box.x + box.width <= width);
      }
      await page.reload(); // direct route compatibility
      await page.getByRole('navigation').waitFor();
      assert.equal(await nav.locator('[aria-current="page"]').textContent(),label);
    }
    await page.goto('http://127.0.0.1:3000/upcoming');
    await page.getByRole('article').first().waitFor();
    await page.getByRole('link',{name:'All recorded predictions',exact:true}).click();
    await page.waitForURL('**/predictions');
    await page.getByRole('link',{name:'View opportunity evidence'}).first().click();
    await page.waitForURL('**/opportunities/**');
    assert.equal(await page.locator('nav [aria-current="page"]').textContent(),'Predictions');
    await page.goBack();
    await page.waitForURL('**/predictions');
    await page.goBack();
    await page.waitForURL('**/upcoming');
    await page.getByRole('article').first().waitFor();
    if (width === 1440 || width === 375) await page.screenshot({path:path.join(__dirname,`${width === 1440 ? 'desktop' : 'mobile'}.png`)});
    // Keyboard links retain native tab/Enter behavior.
    await page.getByRole('navigation').getByRole('link',{name:'Teams',exact:true}).focus();
    await page.keyboard.press('Enter');
    await page.waitForURL('**/teams');
    await page.locator('main a[href^="/teams/"]:visible').first().click();
    await page.waitForURL('**/teams/*');
    assert.equal(await page.locator('nav [aria-current="page"]').textContent(),'Teams');
    await page.goto('http://127.0.0.1:3000/research/btts/performance');
    assert.equal(await page.locator('nav [aria-current="page"]').textContent(),'BTTS Value');
    assert.deepEqual(errors,[]);
    await page.close();
    console.log(`PASS ${width}px: primary links, active states, direct URLs, back, fixture/detail and keyboard navigation; header fits, no page errors (known 320px Recommendations heading overflow excluded)`);
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
