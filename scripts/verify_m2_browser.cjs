/* M2 browser acceptance. Fixture credentials arrive on stdin and are never saved. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

let phase = 'read fixture';
let redactions = [], failureDetails = {};

function safeMessage(message) {
  let text = String(message || '');
  for (const secret of redactions) text = text.split(secret).join('[REDACTED]');
  return text.slice(0, 3000);
}

async function main() {
  const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
  redactions = [fixture.teacher?.password, fixture.student?.password,
    fixture.teacher?.username, fixture.student?.username].filter(Boolean).sort((a, b) => b.length - a.length);
  const {chromium} = require('playwright');
  const baseUrl = String(fixture.base_url || 'http://127.0.0.1:7860').replace(/\/$/, '');
  assert.equal(fixture.questions.length, 20, 'The acceptance fixture must contain 20 questions');
  const outputDir = path.resolve(fixture.output_dir);
  fs.mkdirSync(outputDir, {recursive: true});
  const screenshots = {}, checks = {}, pageErrors = [];
  let browser, teacher, student;

  const apiResponse = (page, endpoint, method = 'GET') => page.waitForResponse(response =>
    new URL(response.url()).pathname === endpoint && response.request().method() === method);

  async function login(page, role, credentials, next) {
    const previousPhase = phase;
    phase = `${role} form login`;
    await page.goto(`${baseUrl}/login?portal=${role}&next=${encodeURIComponent(next)}`);
    await page.locator('#username').fill(credentials.username);
    await page.locator('#password').fill(credentials.password);
    await Promise.all([
      page.waitForURL(url => url.pathname === next),
      page.locator('#btnLogin').click(),
    ]);
    phase = previousPhase;
  }

  async function screenshot(page, name, fullPage = false) {
    const target = path.join(outputDir, `${name}.png`);
    await page.screenshot({path: target, fullPage, animations: 'disabled'});
    screenshots[name] = target;
  }

  async function noHorizontalOverflow(page, name) {
    const dimensions = await page.evaluate(() => ({
      width: window.innerWidth,
      scrollWidth: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
    }));
    assert(dimensions.scrollWidth <= dimensions.width + 1, `${name}: horizontal overflow`);
    checks[name] = true;
  }

  try {
    phase = 'launch Edge';
    browser = await chromium.launch({headless: true, channel: 'msedge'});
    const teacherContext = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'zh-CN', timezoneId: 'Asia/Shanghai'});
    const studentContext = await browser.newContext({viewport: {width: 375, height: 812}, locale: 'zh-CN', timezoneId: 'Asia/Shanghai'});
    teacher = await teacherContext.newPage(); student = await studentContext.newPage();
    for (const page of [teacher, student]) {
      page.setDefaultTimeout(20000);
      page.on('pageerror', error => pageErrors.push(safeMessage(error.message)));
    }

    phase = 'teacher form login and assignment publication';
    await login(teacher, 'teacher', fixture.teacher, '/teacher/assignments');
    await teacher.locator(`#aPaper option[value="${fixture.paper_id}"]`).waitFor({state: 'attached'});
    await teacher.locator('#aTitle').fill(fixture.title);
    await teacher.locator('#aPaper').selectOption(String(fixture.paper_id));
    await teacher.locator('#aClass').selectOption(String(fixture.class_id));
    await teacher.locator('#aSelf').check();
    const [publishedResponse] = await Promise.all([
      apiResponse(teacher, '/api/v1/teacher/assignments', 'POST'),
      teacher.locator('#publishBtn').click(),
    ]);
    assert(publishedResponse.ok(), 'Assignment publication failed');
    const published = await publishedResponse.json(), assignmentId = Number(published.id);
    assert(Number.isInteger(assignmentId) && assignmentId > 0);
    checks.teacher_form_publish = true;

    phase = 'student mobile home';
    await login(student, 'student', fixture.student, '/student');
    const assignmentRow = student.locator('.assignment-item').filter({hasText: fixture.title});
    await assignmentRow.waitFor();
    await student.locator('#classInfo .class-name').waitFor();
    await noHorizontalOverflow(student, 'student_home_375px');
    await screenshot(student, 'student-home-mobile', true);

    phase = 'student answer controls';
    const [detailResponse] = await Promise.all([
      apiResponse(student, `/api/v1/student/assignments/${assignmentId}`),
      assignmentRow.getByRole('link', {name: '开始作答'}).click(),
    ]);
    assert(detailResponse.ok(), 'Student assignment detail failed');
    const detail = await detailResponse.json();
    assert(detail.questions.every(question => !Object.hasOwn(question, 'answer') && !Object.hasOwn(question, 'analysis')));
    checks.answers_hidden_before_submission = true;
    await student.waitForFunction(() => document.querySelectorAll('.answer-card').length === 20);
    checks.twenty_question_cards = true;
    let selfAssessed = false;
    for (const question of fixture.questions) {
      const card = student.locator(`[data-question-card="${question.id}"]`);
      const objective = ['选择题', '单选题', '多选题', '判断题'].includes(question.question_type);
      if (objective) assert.equal(await card.locator('.self-check').count(), 0);
      if (['选择题', '单选题'].includes(question.question_type)) {
        if ((question.options || []).length) await card.locator('input[type="radio"]').first().check();
        else await card.locator('.text-answer').fill('A');
      } else if (question.question_type === '多选题') {
        await card.locator('input[value="A"]').check();
        await card.locator('input[value="C"]').check();
      } else if (question.question_type === '判断题') {
        await card.locator('input[value="正确"]').check();
      } else {
        await card.locator('.text-answer').fill('先整理题目中的已知条件，再列出关系式。\n代入数值并完成推导，最后检查结果。');
        if (!selfAssessed) {
          await card.locator('.self-check').selectOption('true');
          selfAssessed = true;
        }
      }
    }
    assert(selfAssessed);
    assert.equal((await student.locator('#progressText').textContent()).trim(), '20 / 20 已作答');
    checks.twenty_answers_collected = true;
    checks.objective_controls_without_self_check = true;
    await noHorizontalOverflow(student, 'student_answer_375px');
    await student.evaluate(() => window.scrollTo(0, 0));
    await screenshot(student, 'student-answer-mobile');

    phase = 'student submission and objective grading';
    student.once('dialog', dialog => dialog.accept());
    const [submissionResponse, resultPayload] = await Promise.all([
      apiResponse(student, `/api/v1/student/assignments/${assignmentId}/submit`, 'POST'),
      apiResponse(student, `/api/v1/student/assignments/${assignmentId}/result`).then(response => {
        assert(response.ok(), 'Student result retrieval failed');
        return response.json();
      }),
      student.locator('#submitBtn').click(),
    ]);
    phase = 'student submission response';
    assert(submissionResponse.ok(), 'Student submission failed');
    // The POST page navigates immediately; its CDP response body may already be gone.
    // Read the persisted submission from the result request on the destination page.
    const submission = resultPayload.submission;
    assert.equal(Number(submission.auto_score), 93.3);
    const submissionId = Number(submission.id);
    assert(Number.isInteger(submissionId) && submissionId > 0);
    phase = 'student result navigation';
    await student.waitForURL(url => url.pathname === `/student/assignments/${assignmentId}` && url.searchParams.get('result') === '1');
    phase = 'student result score panel';
    await student.locator('#scorePanel').waitFor({state: 'visible'});
    phase = 'student result correct-count and self-assessment assertions';
    assert.equal((await student.locator('.stat').filter({hasText: '客观题正确'}).locator('.v').textContent()).trim(), '14 题');
    assert.equal(await student.locator('.badge').filter({hasText: '自评掌握'}).count(), 1);
    assert.equal(await student.locator('.result-option').count(), fixture.questions.reduce((sum, question) => sum + (question.options || []).length, 0));
    checks.auto_score_93_3 = true;
    checks.self_assessment_excluded_from_objective_count = true;
    checks.result_options_rendered = true;
    await noHorizontalOverflow(student, 'student_result_375px');
    await screenshot(student, 'student-result-mobile');

    phase = 'teacher final grading';
    await teacher.reload();
    const teacherRow = teacher.locator('#assignmentRows tr').filter({hasText: fixture.title});
    await teacherRow.getByRole('button', {name: '详情 / 批改'}).click();
    const scoreInput = teacher.locator(`#score-submission-${submissionId}`);
    await scoreInput.waitFor();
    assert.equal(await teacher.locator('#submissionRows').getByText('自评掌握', {exact: true}).count(), 1);
    await scoreInput.fill('88.5');
    const [gradeResponse] = await Promise.all([
      apiResponse(teacher, `/api/v1/teacher/submissions/${submissionId}/check`, 'POST'),
      teacher.locator(`#save-submission-${submissionId}`).click(),
    ]);
    assert(gradeResponse.ok(), 'Teacher grading failed');
    const grade = await gradeResponse.json();
    assert.equal(Number(grade.final_score), 88.5);
    await teacher.locator('#submissionRows').getByText('已批改', {exact: true}).waitFor();
    await scoreInput.scrollIntoViewIfNeeded();
    await screenshot(teacher, 'teacher-grading');
    checks.teacher_final_score_88_5 = true;
    checks.teacher_self_assessment_label = true;

    phase = 'student graded result and desktop home';
    await student.reload();
    await student.locator('#scorePanel').waitFor({state: 'visible'});
    assert.equal((await student.locator('.stat').filter({hasText: '最终分数'}).locator('.v').textContent()).trim(), '88.5 分');
    await noHorizontalOverflow(student, 'student_checked_result_375px');
    await screenshot(student, 'student-result-checked-mobile');
    checks.student_receives_final_score = true;
    await student.getByRole('link', {name: '← 返回我的学习'}).click();
    await student.locator('.assignment-item').filter({hasText: fixture.title}).getByText('已批改', {exact: true}).waitFor();
    await student.setViewportSize({width: 1440, height: 1000});
    await noHorizontalOverflow(student, 'student_home_1440px');
    await screenshot(student, 'student-home-desktop', true);
    checks.return_to_learning_home = true;
    assert.equal(pageErrors.length, 0, 'Browser JavaScript error detected');
    checks.no_browser_javascript_errors = true;
    process.stdout.write(JSON.stringify({assignment_id: assignmentId, submission_id: submissionId,
      auto_score: submission.auto_score, final_score: grade.final_score, screenshots, checks}));
  } catch (error) {
    failureDetails = {checks, screenshots, page_errors: pageErrors};
    if (!phase.includes('login')) {
      failureDetails.message = safeMessage(error.message);
      for (const [role, page] of [['teacher', teacher], ['student', student]]) {
        if (!page || page.isClosed() || new URL(page.url()).pathname === '/login') continue;
        try { await screenshot(page, `failure-${role}`); }
        catch (_) { /* Preserve the original diagnostic if screenshot capture fails. */ }
      }
    }
    throw error;
  } finally {
    if (browser) await browser.close();
  }
}

main().catch(error => {
  // Login errors omit details; all other diagnostics remove both fixture credentials.
  process.stderr.write(JSON.stringify({error: 'M2 browser acceptance failed', phase,
    type: error.name || 'Error', ...failureDetails}) + '\n');
  process.exitCode = 1;
});
