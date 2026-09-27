// Real browser negative-path acceptance. Credentials arrive only via stdin;
// no storage-state export, HAR, traces, or login screenshots are written.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(path.resolve(process.argv[2]));

(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const target = new URL(input.base_url);
  assert.equal(target.hostname, '127.0.0.1');
  const browser = await chromium.launch({headless:true, executablePath:input.browser_executable || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1280, height:960}});
    const errors = [];
    page.on('pageerror', () => errors.push('pageerror'));
    await page.goto(`${input.base_url}/login?portal=teacher&next=%2Fteacher%2Fknowledge-base`);
    await page.locator('#username').fill(input.username);
    await page.locator('#password').fill(input.password);
    await page.locator('#btnLogin').click();
    await page.waitForURL('**/teacher/knowledge-base');
    await page.locator(`#versionList article[onclick="selectVersion(${input.version_id})"]`).click();
    const row = page.locator('#documentList tr').filter({has:page.locator(`button[onclick="previewDocument(${input.document_id})"]`)});
    await row.getByRole('button', {name:'预览', exact:true}).click();
    await page.locator('[data-approve]').waitFor();
    assert.match(await page.locator('.modal.show').innerText(), /OCR 待人工复核/);
    assert.match(await page.locator('.modal.show pre').first().innerText(), /\|.*0.*1.*2.*\|/);
    await page.screenshot({path:path.join(input.output_dir, 'review-preview.png'), fullPage:true});
    await page.locator('[data-note]').fill('负向测试：没有核对原件，不提交审核');
    await page.locator('[data-approve]').click();
    await page.getByText('请填写说明；通过前须勾选原件核对确认', {exact:true}).waitFor();
    await page.locator('[data-close]').click();
    await row.getByRole('button', {name:'发布', exact:true}).click();
    const responsePromise = page.waitForResponse(r => r.url().endsWith(`/documents/${input.document_id}/publication`) && r.request().method() === 'POST');
    await page.locator('[data-act="ok"]').click();
    const response = await responsePromise;
    assert.equal(response.status(), 400);
    await page.getByText('OCR 资料尚未通过人工复核，或入库任务未完成；请先预览复核', {exact:true}).waitFor();
    await page.screenshot({path:path.join(input.output_dir, 'publication-blocked.png'), fullPage:true});
    assert.deepEqual(errors, []);
    process.stdout.write(JSON.stringify({login:'passed', preview:'passed', checklist_guard:'passed',
      publication_http_status:response.status(), page_errors:errors.length}));
  } finally {
    await browser.close();
  }
})().catch(() => { process.stderr.write('Browser acceptance failed'); process.exitCode = 1; });
