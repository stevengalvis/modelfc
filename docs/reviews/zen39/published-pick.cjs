// Synthetic browser acceptance against the same local live-mode server as states.cjs.
// No provider/production calls: all API reads are intercepted; publication is unchanged.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fixture = require('../../../tests/fixtures/strategy_picks.json');
const bttsFixture = require('../../../tests/fixtures/btts_performance.json');
const pick = fixture.publications[0];
const blank = Object.fromEntries(Object.entries(fixture.summary).map(([k,v]) => [k,v === null ? null : 0]));
const emptyStrategy = {...fixture,strategy:'higher_probability',summary:blank,totals:blank,market_totals:[],by_league_market:[],publications:[],total:0};
const emptyResearch = {...bttsFixture,competition:'E1',coverage:'SUPPORTED',forecasts:[],unique_fixtures:0,forecast_cohorts:0,settled_fixtures:0,unresolved_fixtures:0,upcoming_fixtures:0,metrics:{sample_count:0,brier_score:null,log_loss:null,calibration:[]}};
(async () => {
  const browser = await chromium.launch({executablePath:process.env.BROWSER_PATH || '/usr/bin/chromium',args:['--no-sandbox']});
  for (const width of [1440,375,320]) {
    const page = await browser.newPage({viewport:{width,height:900}});
    await page.clock.setFixedTime(new Date('2026-10-21T12:00:00Z'));
    const reads = [];
    const errors = [];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('http://127.0.0.1:3999/api/v1/**',route=>{
      const url = new URL(route.request().url());
      reads.push(url.pathname);
      const body = url.pathname === '/api/v1/prospective/performance' ? (url.searchParams.get('strategy') === 'best_value' ? fixture : emptyStrategy)
        : url.pathname === '/api/v1/research/btts/performance' ? emptyResearch : [];
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });
    const fits = async () => assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), `overflow at ${width} ${page.url()}`);
    const frozen = async () => {
      assert.equal(await page.locator(`time[datetime="${pick.offer.retrieved_at_utc}"]`).count(),1);
      assert.equal(await page.locator(`time[datetime="${pick.published_at_utc}"]`).count(),1);
      await page.getByText('Stale quote · price needs rechecking.',{exact:true}).waitFor();
      await page.getByText('Frozen publication price · Quote recorded', {exact:false}).waitFor();
    };
    await page.goto('http://127.0.0.1:3001/upcoming');
    await page.getByText('Both teams to score: Yes',{exact:true}).waitFor();
    assert.equal(await page.getByRole('article').count(),1);
    await page.getByRole('heading',{name:'A vs D',exact:true}).waitFor();
    await page.getByText('draftkings · +120 · 67.0% model probability',{exact:true}).waitFor();
    await page.getByText('Legacy opportunities',{exact:true}).click();
    await page.getByText('No legacy corner opportunity',{exact:false}).waitFor();
    await page.getByText('Saved observations do not replace frozen publication odds or probabilities.',{exact:false}).waitFor();
    await frozen(); await fits();
    assert(reads.includes('/api/v1/recommendations'), 'empty legacy feed was read');
    await page.getByRole('link',{name:'All published picks',exact:true}).click();
    await page.waitForURL('**/recommendations');
    await page.getByRole('tab',{name:'Best Value',exact:true}).click();
    const card = page.getByRole('article',{name:'A vs D',exact:true});
    await card.waitFor();
    assert.equal(await page.getByRole('article').count(),1);
    await card.getByText('Both teams to score: Yes',{exact:true}).waitFor();
    await card.getByText('draftkings',{exact:true}).waitFor();
    await card.getByText('+120',{exact:true}).waitFor();
    await card.getByText('67.0%',{exact:false}).waitFor();
    await card.getByText('Recorded price · not live',{exact:true}).waitFor();
    await card.getByRole('button',{name:'Pick information for A vs D',exact:false}).click();
    await card.getByText(`Publication: ${pick.publication_id}`,{exact:false}).waitFor();
    await card.getByText('Availability is not verified live.',{exact:false}).waitFor();
    await card.getByText('Newer prices never replace this publication.',{exact:false}).waitFor();
    await frozen(); await fits();
    if (width === 320) await page.screenshot({path:path.join(__dirname,'published-pick-320.png'),fullPage:true});
    await page.goBack();
    await page.waitForURL('**/upcoming');
    await page.getByText('draftkings · +120 · 67.0% model probability',{exact:true}).waitFor();
    await frozen(); await fits();
    assert.deepEqual(errors,[]);
    await page.close();
    console.log(`PASS ${width}px: unchanged published fixture across Upcoming/Recommendations/Back, empty legacy feed, frozen price/times, not-live/stale warnings, no overflow`);
  }
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
