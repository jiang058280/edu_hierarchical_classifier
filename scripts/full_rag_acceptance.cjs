const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(path.resolve(process.argv[2]));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const report = {stage:'launch', checks:{}};
function save(stage) { report.stage=stage; fs.writeFileSync(path.join(input.output,'browser_report.json'),JSON.stringify(report,null,2)); }

(async()=>{
  assert.equal(new URL(input.base_url).hostname,'127.0.0.1');
  const browser=await chromium.launch({headless:true,executablePath:input.chromium});
  const errors=[];
  async function login(name, route, portal) {
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage();
    page.setDefaultTimeout(90000);
    page.on('pageerror',()=>errors.push('pageerror'));
    await page.goto(`${input.base_url}/login?portal=${portal}&next=${encodeURIComponent(route)}`);
    await page.locator('#username').fill(input.users[name].username);
    await page.locator('#password').fill(input.users[name].password);
    await page.locator('#btnLogin').click(); await page.waitForURL('**'+route);
    return page;
  }
  const row=(page,id)=>page.locator('#documentList tr').filter({has:page.locator(`button[onclick="previewDocument(${id})"]`)});
  try {
    save('teacher_login');
    const teacher=await login('teacher','/teacher/knowledge-base','teacher');
    await teacher.locator('#versionName').fill('隔离端到端验收');
    let response=teacher.waitForResponse(r=>r.url().endsWith('/teacher/rag/versions')&&r.request().method()==='POST');
    await teacher.getByRole('button',{name:'新建版本',exact:true}).click();
    let result=await (await response).json(); report.version_id=result.id;
    save('upload_scan');
    await teacher.locator('#sourceFile').setInputFiles(input.pdf);
    await teacher.locator('#subject').fill('数学'); await teacher.locator('#gradeBand').selectOption('初中'); await teacher.locator('#grade').fill('初二');
    response=teacher.waitForResponse(r=>r.url().endsWith('/documents/upload'));
    await teacher.locator('#uploadBtn').click(); result=await(await response).json();
    assert.ok(result.document_id); report.original_document_id=result.document_id;
    await row(teacher,result.document_id).getByRole('button',{name:'预览',exact:true}).click();
    await teacher.locator('[data-correction]').waitFor({state:'attached'});
    const originalPreview=await teacher.evaluate(async id=>T.api(`/api/v1/teacher/rag/documents/${id}`),result.document_id);
    report.automatic_corrections=originalPreview.ocr_review.automatic_corrections;
    const text=originalPreview.ocr_review.parent_texts.join('\n').replace(/\s/g,'');
    assert.ok(text.includes('k<0') && text.includes('2×2+1=5') && text.includes('|x|0|1|2|'));
    report.checks.automatic_math_repair=true;
    save('online_correction');
    await teacher.getByText('在线校对并创建新版本',{exact:true}).click();
    await teacher.locator('[data-correction]').fill(input.corrected_text);
    // Exercise the real input listener: it must preserve every table/paragraph newline.
    await teacher.locator('[data-correction]').dispatchEvent('input');
    assert.equal(await teacher.locator('[data-correction]').inputValue(),input.corrected_text);
    await teacher.locator('[data-correction-note]').fill('隔离测试：对照原页核对全文与数学符号，修正页脚 OCR 字样');
    response=teacher.waitForResponse(r=>r.url().endsWith('/correction')&&r.request().method()==='POST');
    await teacher.locator('[data-save-correction]').click();
    result=await(await response).json(); assert.ok(result.document_id); report.corrected_document_id=result.document_id;
    await teacher.locator('[data-approve]').waitFor();
    const blocked=await teacher.evaluate(async id=>{
      const r=await fetch(`/api/v1/teacher/rag/documents/${id}/publication`,{method:'POST',headers:{Authorization:'Bearer '+T.token(),'Content-Type':'application/json'},body:JSON.stringify({published:true})}); return r.status;
    },result.document_id);
    assert.equal(blocked,400); report.checks.correction_requires_review=true;
    save('review_publish_activate');
    await teacher.locator('[data-checked]').check(); await teacher.locator('[data-note]').fill('隔离验收：已逐项对照原件核对，校对版公式、表格、正文正确');
    response=teacher.waitForResponse(r=>r.url().endsWith('/ocr-review'));
    await teacher.locator('[data-approve]').click(); assert.equal((await response).status(),200);
    await row(teacher,result.document_id).getByRole('button',{name:'发布',exact:true}).click();
    response=teacher.waitForResponse(r=>r.url().endsWith('/publication'));
    await teacher.locator('[data-act="ok"]').click(); assert.equal((await response).status(),200);
    await teacher.locator(`#versionList article[onclick="selectVersion(${report.version_id})"]`).getByRole('button',{name:'检查并激活'}).click();
    response=teacher.waitForResponse(r=>r.url().endsWith('/activate'));
    await teacher.locator('[data-act="ok"]').click(); assert.equal((await response).status(),200);
    report.checks.teacher_workflow=true;
    await teacher.screenshot({path:path.join(input.output,'teacher-published.png'),fullPage:true});
    save('student_stream');
    const student=await login('student','/student/qa','student');
    await student.locator('#queryInput').fill('已知 y = 2x + 1，当 x = 2 时如何计算？');
    response=student.waitForResponse(r=>r.url().endsWith('/student/rag/chat/stream'));
    await student.locator('#sendBtn').click();
    const stream=await response; assert.equal(stream.status(),200);
    const sse=await stream.text();
    assert.ok(sse.includes('event: done')&&!sse.includes('event: error'));
    const sessions=await student.evaluate(()=>T2.api('/api/v1/student/rag/sessions'));
    report.session_id=sessions.items[0].id;
    const messages=await student.evaluate(id=>T2.api(`/api/v1/student/rag/sessions/${id}/messages`),report.session_id);
    const answer=messages.items.filter(m=>m.role==='assistant').at(-1);
    assert.ok(answer && !answer.refused && answer.content.includes('5') && answer.citations.length);
    assert.ok(answer.citations.every(c=>c.source_name.includes('在线校对')));
    report.student_answer=answer.content; report.student_citations=answer.citations;
    report.checks.student_stream_persisted=true;
    await student.locator('#dialog .citation').first().waitFor();
    await student.screenshot({path:path.join(input.output,'student-answer.png'),fullPage:true});
    await student.reload();
    await student.locator('.qa-session').first().click();
    await student.locator('#dialog .citation').first().waitFor();
    report.checks.history_reload=true;
    save('authorization_and_scope');
    const other=await login('other','/student/qa','student');
    const access=await other.evaluate(async ids=>{
      const h={Authorization:'Bearer '+T2.token()};
      const session=await fetch(`/api/v1/student/rag/sessions/${ids.session}/messages`,{headers:h});
      const source=await fetch(`/api/v1/teacher/rag/documents/${ids.doc}/source`,{headers:h});
      return [session.status,source.status];
    },{session:report.session_id,doc:report.original_document_id});
    assert.deepEqual(access,[404,403]); report.checks.authorization=true;
    await other.locator('#queryInput').fill('已知 y = 2x + 1，当 x = 2 时如何计算？');
    response=other.waitForResponse(r=>r.url().endsWith('/student/rag/chat/stream'));
    await other.locator('#sendBtn').click(); const deniedStream=await(await response).text();
    assert.ok(deniedStream.includes('event: done')&&deniedStream.includes('"refused": true'));
    report.checks.grade_scope_refusal=true;
    assert.deepEqual(errors,[]); report.checks.no_page_errors=true;
    save('passed');
  } catch(error) { report.error_type=error.name; save(report.stage); throw error; }
  finally {await browser.close();}
})().catch(()=>{process.stderr.write('Acceptance failed; inspect stage report');process.exitCode=1;});
