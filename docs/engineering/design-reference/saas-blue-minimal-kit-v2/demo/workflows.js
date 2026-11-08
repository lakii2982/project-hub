/* Static, no-network interaction examples. No production data or API requests. */
(() => {
  'use strict';
  const sceneNames = {project:['新建项目','以最少必要字段完成创建，复杂信息后续补充。'],timesheet:['工时填报','明确草稿、已提交与已审批是不同业务状态。'],expense:['成本录入','展示项目成本的录入、归集及财务语义。'],cash:['现金收付','分离真实收支与计划收支，避免经营指标口径混用。'],approval:['审批处理','通过详情抽屉完成审批，并展示意见与结果反馈。']};
  const $ = (selector, base = document) => base.querySelector(selector);
  const $$ = (selector, base = document) => [...base.querySelectorAll(selector)];
  const formatNumber = number => new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(number);
  let toastTimer;
  function toast(message) {
    const element = $('#toast');
    element.textContent = message;
    element.classList.add('shown');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => element.classList.remove('shown'), 3800);
  }
  function navigate(scene) {
    const next = sceneNames[scene] ? scene : 'project';
    $$('.scene').forEach(node => node.hidden = node.id !== 'scene-' + next);
    $$('.navlink').forEach(node => {node.classList.toggle('selected',node.dataset.scene === next);node.setAttribute('aria-current',node.dataset.scene === next ? 'page' : 'false');});
    $('#page-title').textContent = sceneNames[next][0];
    $('#crumb').textContent = sceneNames[next][0];
    $('#page-subtitle').textContent = sceneNames[next][1];
    if (location.hash !== '#' + next) history.replaceState(null, '', '#' + next);
    window.scrollTo({top:0,behavior:'instant'});
  }
  $$('.navlink').forEach(button => button.addEventListener('click',() => navigate(button.dataset.scene)));
  window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
  navigate(location.hash.slice(1) || 'project');

  function fieldError(id, message) {
    const field = $('#'+id);
    const error = $('#error-'+id);
    if (error) error.textContent = message;
    if (field) {if (message) {field.setAttribute('aria-invalid','true');if(error) field.setAttribute('aria-describedby',error.id);} else {field.removeAttribute('aria-invalid');field.removeAttribute('aria-describedby');}}
    return !message;
  }
  const projectForm = $('#project-form');
  projectForm.addEventListener('submit',event => {
    event.preventDefault();
    const name = $('#project-name').value.trim();
    const client = $('#project-client').value;
    const owner = $('#project-owner').value;
    const start = $('#project-start').value;
    const end = $('#project-end').value;
    const budget = $('#project-budget').value;
    const contract = $('#project-contract').value;
    const tests = [
      ['project-name',name ? '' : '请填写项目名称'],
      ['project-client',client ? '' : '请选择客户'],
      ['project-owner',owner ? '' : '请选择负责人'],
      ['project-start',start ? '' : '请选择开始日期'],
      ['project-end',!end ? '请选择结束日期' : (start && end < start ? '结束日期不能早于开始日期' : '')]
    ];
    let passed = true;
    tests.forEach(([id,message]) => {if (!fieldError(id,message)) passed=false;});
    if (budget !== '' && (!Number.isFinite(+budget) || +budget < 0)) {toast('预算金额不能小于 0');passed = false;}
    if (contract !== '' && (!Number.isFinite(+contract) || +contract < 0)) {toast('合同金额不能小于 0');passed = false;}
    if (!passed) {const first = $('[aria-invalid="true"]',projectForm);first?.focus();return;}
    toast(`已模拟创建“${name}”。此演示不会保存项目或调用 API。`);
  });
  projectForm.addEventListener('reset',() => {setTimeout(() => $$('[aria-invalid]',projectForm).forEach(el => fieldError(el.id,'')),0);toast('表单已恢复到初始演示值');});
  $$('input,select',projectForm).forEach(el => el.addEventListener('input',() => {if(el.id.startsWith('project-')) fieldError(el.id,'');}));

  const hourInputs = $$('.hour-input');
  let hoursSubmitted = false;
  function recalcHours() {
    const rows = $$('#scene-timesheet tbody tr');
    let total = 0, valid = true, count = 0;
    const daily = Array(7).fill(0);
    rows.forEach(row => {
      let rowTotal = 0;
      $$('.hour-input', row).forEach((input,i) => {
        const value = input.value.trim() === '' ? 0 : Number(input.value);
        const invalid = !Number.isFinite(value) || value < 0 || value > 24 || !Number.isInteger(value*2);
        input.setAttribute('aria-invalid',invalid ? 'true' : 'false');
        if(invalid) valid = false;
        else {rowTotal += value;daily[i]+=value;if(value>0)count++;}
      });
      $('.rowtotal',row).textContent = rowTotal.toFixed(1)+' h';
      total += rowTotal;
    });
    if(daily.some(h => h>24)) valid = false;
    $('#hours-total').innerHTML = `${total.toFixed(1)} <small>h</small>`;
    $('#hours-count').innerHTML = `${count} <small>格</small>`;
    $('#hours-hint').textContent = valid ? '此处只演示 0.5 小时间隔与每天不超过 24 小时的输入校验；生产规则以现有系统为准。' : '填写存在问题：单格范围应为 0–24 h、0.5 h 递增；单日合计也不得超过 24 h。';
    $('#hours-hint').style.color = valid ? '' : '#DC2626';
    return valid;
  }
  hourInputs.forEach(input => input.addEventListener('input',()=>{hoursSubmitted=false; $('#hours-status').textContent='草稿';$('#hours-status').className='status warning';recalcHours();}));
  $('#hours-save').addEventListener('click',()=> {if(recalcHours()) toast('已模拟保存工时草稿；仅保留于当前页面。'); else toast('工时输入不符合演示校验规则，请检查。');});
  $('#hours-submit').addEventListener('click',()=> {if(!recalcHours()){toast('请先修正工时输入错误。');return;}if(hoursSubmitted){toast('这份演示工时已提交，请勿重复操作。');return;}hoursSubmitted=true;$('#hours-status').textContent='已提交 · 待审批';$('#hours-status').className='status warning';toast('已模拟提交审批；并未进入生产审核流程或成本归集。');});
  recalcHours();

  $('#expense-form').addEventListener('submit',event => {
    event.preventDefault();
    const amount = Number($('#expense-amount').value);
    const valid = $('#expense-amount').value.trim()!=='' && Number.isFinite(amount) && amount>0;
    fieldError('expense-amount',valid?'':'请输入大于 0 的成本金额');
    if (!valid) {$('#expense-amount').focus();return;}
    toast(`已模拟录入成本 ¥ ${formatNumber(amount)}；不会生成付款交易。`);
  });
  $('#expense-amount').addEventListener('input',()=>fieldError('expense-amount',''));
  $('#expense-form').addEventListener('reset',()=>setTimeout(()=>fieldError('expense-amount',''),0));

  const cashStateRadios = $$('input[name="cash-state"]');
  cashStateRadios.forEach(input => input.addEventListener('change',() => {
    const planned = $('input[name="cash-state"]:checked').value === 'planned';
    $('#cash-state-hint').textContent = planned ? '计划收付不会计入已收/已付或资金余额；仅用于预测。' : '实际收付应有到账/付款依据，并按现有系统校验及对账。';
  }));
  $('#cash-form').addEventListener('submit',event => {
    event.preventDefault();
    const amount = Number($('#cash-amount').value);
    const valid = $('#cash-amount').value.trim()!=='' && Number.isFinite(amount) && amount>0;
    fieldError('cash-amount',valid?'':'请输入大于 0 的收付金额');
    if (!valid) {$('#cash-amount').focus();return;}
    const planned = $('input[name="cash-state"]:checked').value === 'planned';
    const inMoney = $('input[name="cash-direction"]:checked').value === 'in';
    toast(`已模拟记录${planned?'计划':'实际'}${inMoney?'收款':'付款'} ¥ ${formatNumber(amount)}；无真实记账或余额变化。`);
  });
  $('#cash-amount').addEventListener('input',()=>fieldError('cash-amount',''));
  $('#cash-form').addEventListener('reset',()=>setTimeout(()=>{fieldError('cash-amount','');$('#cash-state-hint').textContent='只有真实到账/付款记录才可参与“实际现金流”汇总。';},0));

  const approvalData = {
    timesheet: {title:'工时审批',owner:'李明',project:'客户官网改版',amount:'18 小时',submitted:'2026-10-27',type:'工时填报'},
    expense: {title:'成本报销',owner:'王芳',project:'客户官网改版',amount:'¥ 8,600.00',submitted:'2026-10-26',type:'外包服务'},
    payment: {title:'付款申请',owner:'张磊',project:'电商平台开发',amount:'¥ 15,000.00',submitted:'2026-10-25',type:'供应商付款'}
  };
  let activeApproval = null, previousFocus = null;
  const overlay = $('#approval-overlay');
  function openDrawer(key) {
    const data = approvalData[key]; if(!data) return;
    activeApproval = key; previousFocus = document.activeElement;
    $('#drawer-title').textContent = data.title;
    $('#review-note').value=''; $('#review-error').textContent='';
    $('#approval-detail').innerHTML = [['申请人',data.owner],['关联项目',data.project],['业务类型',data.type],['申请数量 / 金额',data.amount],['提交日期',data.submitted]].map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('');
    overlay.hidden = false;
    document.body.style.overflow='hidden';
    $('.icon-close',overlay).focus();
  }
  function closeDrawer() {overlay.hidden = true; document.body.style.overflow='';activeApproval = null;previousFocus?.focus();}
  $$('[data-open-approval]').forEach(btn=>btn.addEventListener('click',()=>openDrawer(btn.dataset.openApproval)));
  $$('[data-close-drawer]').forEach(el=>el.addEventListener('click',closeDrawer));
  document.addEventListener('keydown',event => {
    if(overlay.hidden) return;
    if(event.key === 'Escape') {event.preventDefault();closeDrawer();return;}
    if(event.key === 'Tab') {
      const focusables = $$('button, input, textarea, select, a[href]',overlay).filter(el=>!el.disabled && el.getClientRects().length);
      const first=focusables[0],last=focusables.at(-1);
      if(event.shiftKey && document.activeElement===first) {event.preventDefault();last.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
    }
  });
  function decide(approved) {
    if(!activeApproval)return;
    const note = $('#review-note').value.trim();
    if(!approved && !note){$('#review-error').textContent='驳回时请填写审批意见（演示规则）';$('#review-note').focus();return;}
    const btn = $(`[data-open-approval="${activeApproval}"]`);
    const item = btn?.closest('.approval-item');
    const state = $('.status',item);
    state.textContent = approved ? '已同意' : '已驳回';
    state.className = approved ? 'status success' : 'status error';
    btn.textContent='查看结果'; btn.disabled=true;
    const result = approved ? '同意' : '驳回';
    closeDrawer();
    toast(`已模拟${result}该申请；未调用审批 API，不产生真实审计记录。`);
  }
  $('#approve-approval').addEventListener('click',()=>decide(true));
  $('#reject-approval').addEventListener('click',()=>decide(false));
})();
