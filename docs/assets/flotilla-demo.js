(() => {
  "use strict";

  const snapshotNode = document.getElementById("flotilla-snapshot");
  if (!snapshotNode) {
    return;
  }
  const snapshot = JSON.parse(snapshotNode.textContent);
  const elements = {
    scenario: document.getElementById("scenario-select"),
    thesis: document.getElementById("thesis-select"),
    budget: document.getElementById("budget-control"),
    budgetOutput: document.getElementById("budget-output"),
    launch: document.getElementById("launch-button"),
    step: document.getElementById("step-button"),
    kill: document.getElementById("kill-button"),
    challenge: document.getElementById("challenge-button"),
    revive: document.getElementById("revive-button"),
    reallocate: document.getElementById("reallocate-button"),
    reset: document.getElementById("reset-button"),
    status: document.getElementById("sim-status"),
    clock: document.getElementById("sim-clock"),
    reserve: document.getElementById("reserve-readout"),
    trajectories: document.getElementById("trajectory-graph"),
    allocation: document.getElementById("allocation-graph"),
    evidenceTitle: document.getElementById("evidence-title"),
    evidenceBody: document.getElementById("evidence-body"),
    lineage: document.getElementById("interactive-lineage"),
  };

  const scenarioLabels = {
    registered: "Registered outcome",
    headwinds: "Replication headwinds",
    recovery: "Signal recovery",
    thin: "Thin evidence",
  };
  let state;

  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  function freshState() {
    return {
      scenario: elements.scenario.value,
      budget: Number(elements.budget.value),
      launched: false,
      round: 0,
      released: 0,
      queue: [],
      events: [
        {
          kind: "SIMULATOR_READY",
          thesis: "portfolio",
          detail: scenarioLabels[elements.scenario.value],
        },
      ],
      theses: snapshot.theses.map((item) => ({
        ...item,
        state: "REGISTERED",
        spent: 0,
        earmarked: 0,
        lastEvidence: item.falsifier_scores,
      })),
    };
  }

  function selectedThesis() {
    return state.theses.find((item) => item.id === elements.thesis.value);
  }

  function reserve() {
    const committed = state.theses.reduce(
      (total, item) => total + item.spent + item.earmarked,
      0,
    );
    return Math.max(0, state.budget - committed);
  }

  function addEvent(kind, thesis, detail) {
    state.events.push({ kind, thesis, detail });
  }

  function outcomeFor(thesis) {
    if (state.scenario === "thin" && thesis.id === "T-05") {
      return "UNDETERMINED";
    }
    if (state.scenario === "headwinds" && thesis.id === "T-04") {
      return "KILL";
    }
    return thesis.registered_status === "KILLED" ? "KILL" : "CONTINUE";
  }

  function evidenceFor(thesis) {
    const scores = { ...thesis.falsifier_scores };
    if (state.scenario === "headwinds" && thesis.id === "T-04") {
      scores.delta = 0.004;
      scores.ci_lower = -0.002;
      scores.ci_upper = 0.01;
    }
    if (state.scenario === "thin" && thesis.id === "T-05") {
      delete scores.ci_lower;
      scores.note = "ci_lower missing from temporary result";
    }
    if (state.scenario === "recovery" && thesis.id === "T-01") {
      scores.recovery_probe = 0.024;
      scores.note = "new registered arm is available after a confirmed stop";
    }
    return scores;
  }

  function consumeCapital(thesis, amount) {
    const available = thesis.earmarked + reserve();
    if (available + 1e-9 < amount) {
      return false;
    }
    const fromEarmark = Math.min(thesis.earmarked, amount);
    thesis.earmarked -= fromEarmark;
    thesis.spent += amount;
    return true;
  }

  function launch() {
    if (state.launched) {
      return;
    }
    state.launched = true;
    state.queue = state.theses.map((item) => ({
      thesisId: item.id,
      kind: "falsifier",
      cost: item.falsifier_cost,
    }));
    state.theses.forEach((item) => {
      item.state = "QUEUED";
    });
    addEvent(
      "FLEET_LAUNCHED",
      "portfolio",
      `${state.queue.length} equal-cost falsifiers queued`,
    );
    elements.status.textContent =
      "Falsifiers launched. Advance one experiment at a time.";
    render();
  }

  function advance() {
    const pending = state.theses.find((item) => item.state === "PENDING_KILL");
    if (pending) {
      elements.thesis.value = pending.id;
      elements.status.textContent =
        `${pending.id} needs a reviewer decision before the fleet advances.`;
      render();
      return;
    }
    const job = state.queue.shift();
    if (!job) {
      elements.status.textContent =
        "No experiments remain. Audit the lineage or reset the simulator.";
      render();
      return;
    }
    const thesis = state.theses.find((item) => item.id === job.thesisId);
    if (!consumeCapital(thesis, job.cost)) {
      thesis.state = "BLOCKED_BUDGET";
      addEvent(
        "EXPERIMENT_BLOCKED",
        thesis.id,
        `${job.kind} needs ${job.cost.toFixed(1)} units`,
      );
      elements.status.textContent =
        `${thesis.id} was blocked by the current mandate.`;
      state.round += 1;
      render();
      return;
    }
    state.round += 1;
    elements.thesis.value = thesis.id;
    thesis.lastEvidence = evidenceFor(thesis);
    addEvent(
      "RUN_COMPLETED",
      thesis.id,
      `${job.kind} spent ${job.cost.toFixed(1)} units`,
    );
    if (job.kind === "falsifier") {
      const outcome = outcomeFor(thesis);
      if (outcome === "KILL") {
        thesis.state = "PENDING_KILL";
        addEvent(
          "PENDING_KILL",
          thesis.id,
          `predicate fired: ${thesis.predicate}`,
        );
        elements.status.textContent =
          `${thesis.id} is pending stop. Confirm or overturn it.`;
      } else if (outcome === "UNDETERMINED") {
        thesis.state = "UNDETERMINED";
        addEvent(
          "UNDETERMINED",
          thesis.id,
          "required score was missing; no verdict inferred",
        );
        elements.status.textContent =
          `${thesis.id} is undetermined. Fund a follow-up or leave it unresolved.`;
      } else {
        thesis.state = "FALSIFIER_SURVIVED";
        state.queue.push({
          thesisId: thesis.id,
          kind: "follow-up",
          cost: thesis.followup_cost,
        });
        addEvent(
          "CONTINUE",
          thesis.id,
          "predicate held; follow-up queued behind the fair floor",
        );
        elements.status.textContent =
          `${thesis.id} survived its falsifier; follow-up queued.`;
      }
    } else {
      thesis.state = thesis.state === "REVIVED" ? "REVIVED" : "SURVIVED";
      addEvent(
        thesis.state === "REVIVED" ? "REVIVAL_CONFIRMED" : "PROMOTE",
        thesis.id,
        "registered follow-up completed",
      );
      elements.status.textContent =
        `${thesis.id} completed its follow-up course.`;
    }
    render();
  }

  function confirmStop() {
    const thesis = selectedThesis();
    if (!thesis || thesis.state !== "PENDING_KILL") {
      return;
    }
    const releasedNow = thesis.earmarked;
    thesis.earmarked = 0;
    thesis.state = "KILLED";
    state.released += releasedNow;
    addEvent(
      "KILL_CONFIRMED",
      thesis.id,
      `${releasedNow.toFixed(1)} earmarked units returned to reserve`,
    );
    elements.status.textContent =
      `${thesis.id} stopped. Evidence retained; revival remains available.`;
    render();
  }

  function overturnStop() {
    const thesis = selectedThesis();
    if (
      !thesis ||
      !["PENDING_KILL", "UNDETERMINED"].includes(thesis.state)
    ) {
      return;
    }
    const prior = thesis.state;
    thesis.state = "FALSIFIER_SURVIVED";
    if (
      !state.queue.some(
        (job) => job.thesisId === thesis.id && job.kind === "follow-up",
      )
    ) {
      state.queue.push({
        thesisId: thesis.id,
        kind: "follow-up",
        cost: thesis.followup_cost,
      });
    }
    addEvent(
      prior === "UNDETERMINED" ? "UNDETERMINED_ESCALATED" : "KILL_OVERTURNED",
      thesis.id,
      "reviewer authorized one registered follow-up",
    );
    elements.status.textContent =
      `${thesis.id} returned to the fleet with a follow-up queued.`;
    render();
  }

  function revive() {
    const thesis = selectedThesis();
    if (!thesis || thesis.state !== "KILLED") {
      return;
    }
    thesis.state = "REVIVED";
    if (
      !state.queue.some(
        (job) => job.thesisId === thesis.id && job.kind === "follow-up",
      )
    ) {
      state.queue.push({
        thesisId: thesis.id,
        kind: "follow-up",
        cost: thesis.followup_cost,
      });
    }
    addEvent(
      "REVIVE",
      thesis.id,
      state.scenario === "recovery"
        ? "new registered arm supplied recovery evidence"
        : "reviewer recorded a reversible challenge",
    );
    elements.status.textContent =
      `${thesis.id} revived; the original stop remains in lineage.`;
    render();
  }

  function reallocate() {
    const thesis = selectedThesis();
    if (!thesis || ["KILLED", "BLOCKED_BUDGET"].includes(thesis.state)) {
      elements.status.textContent =
        "Select a live thesis before reallocating reserve.";
      return;
    }
    if (reserve() < 1) {
      elements.status.textContent = "No full unit remains in reserve.";
      return;
    }
    thesis.earmarked += 1;
    addEvent(
      "CAPITAL_REALLOCATED",
      thesis.id,
      "1.0 reserve unit earmarked for this thesis",
    );
    elements.status.textContent = `One unit earmarked for ${thesis.id}.`;
    render();
  }

  function progressFor(item) {
    const progress = {
      REGISTERED: 7,
      QUEUED: 18,
      FALSIFIER_SURVIVED: 58,
      PENDING_KILL: 48,
      UNDETERMINED: 48,
      KILLED: 48,
      BLOCKED_BUDGET: 68,
      SURVIVED: 100,
      REVIVED: 100,
    };
    return progress[item.state] ?? 7;
  }

  function renderTrajectories() {
    elements.trajectories.innerHTML = state.theses
      .map(
        (item) => `
          <div class="trajectory-lane" data-state="${escapeHtml(item.state)}">
            <button type="button" class="lane-select" data-thesis="${escapeHtml(item.id)}"
              aria-pressed="${item.id === elements.thesis.value}">
              <span>${escapeHtml(item.id)}</span>
              <strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong>
            </button>
            <div class="lane-water" aria-hidden="true">
              <i class="lane-wake" style="--progress:${progressFor(item)}%"></i>
            </div>
            <span class="lane-state">${escapeHtml(item.state.replaceAll("_", " "))}</span>
          </div>`,
      )
      .join("");
    elements.trajectories.querySelectorAll(".lane-select").forEach((button) => {
      button.addEventListener("click", () => {
        elements.thesis.value = button.dataset.thesis;
        render();
      });
    });
  }

  function renderAllocation() {
    const rows = state.theses.map((item) => {
      const spentShare = (item.spent / state.budget) * 100;
      const earmarkShare = (item.earmarked / state.budget) * 100;
      return `
        <div class="allocation-row ${item.state === "KILLED" ? "killed" : ""}">
          <span>${escapeHtml(item.id)}</span>
          <div class="allocation-track" aria-hidden="true">
            <i class="allocation-spent" style="width:${spentShare}%"></i>
            <i class="allocation-earmark" style="width:${earmarkShare}%"></i>
          </div>
          <strong>${(item.spent + item.earmarked).toFixed(1)}u</strong>
        </div>`;
    });
    const reserveShare = (reserve() / state.budget) * 100;
    rows.push(`
      <div class="allocation-row reserve">
        <span>Reserve</span>
        <div class="allocation-track" aria-hidden="true">
          <i class="allocation-spent" style="width:${reserveShare}%"></i>
        </div>
        <strong>${reserve().toFixed(1)}u</strong>
      </div>`);
    elements.allocation.innerHTML = rows.join("");
  }

  function renderEvidence() {
    const thesis = selectedThesis();
    if (!thesis) {
      return;
    }
    elements.evidenceTitle.textContent = `${thesis.id} · ${thesis.title}`;
    const scores = thesis.lastEvidence || {};
    const scoreRows = Object.entries(scores)
      .map(
        ([key, value]) =>
          `<tr><td>${escapeHtml(key)}</td><td>${escapeHtml(
            typeof value === "number" ? value.toFixed(4) : value,
          )}</td></tr>`,
      )
      .join("");
    elements.evidenceBody.innerHTML = `
      <p><strong>Live state</strong><br>${escapeHtml(thesis.state.replaceAll("_", " "))}</p>
      <p><strong>Capital</strong><br>${thesis.spent.toFixed(1)} spent ·
      ${thesis.earmarked.toFixed(1)} earmarked · ${thesis.budget_cap.toFixed(1)} registered cap</p>
      <p><strong>Executable predicate</strong></p>
      <code>${escapeHtml(thesis.predicate)}</code>
      <table class="evidence-scores"><tbody>${scoreRows}</tbody></table>
      <p><strong>Limitations</strong><br>${thesis.limitations.map(escapeHtml).join("<br>")}</p>`;
  }

  function renderLineage() {
    elements.lineage.innerHTML = state.events
      .map(
        (event, index) => `
          <li><span>${String(index).padStart(2, "0")}</span>
          <strong>${escapeHtml(event.kind.replaceAll("_", " "))} ·
          ${escapeHtml(event.thesis)}</strong><em>${escapeHtml(event.detail)}</em></li>`,
      )
      .join("");
    elements.lineage.scrollTop = elements.lineage.scrollHeight;
  }

  function renderButtons() {
    const thesis = selectedThesis();
    elements.launch.disabled = state.launched;
    elements.step.disabled = !state.launched || state.queue.length === 0;
    elements.kill.disabled = !thesis || thesis.state !== "PENDING_KILL";
    elements.challenge.disabled =
      !thesis || !["PENDING_KILL", "UNDETERMINED"].includes(thesis.state);
    elements.challenge.textContent =
      thesis && thesis.state === "UNDETERMINED"
        ? "Fund follow-up"
        : "Overturn stop";
    elements.revive.disabled = !thesis || thesis.state !== "KILLED";
    elements.reallocate.disabled =
      !state.launched ||
      !thesis ||
      reserve() < 1 ||
      ["KILLED", "BLOCKED_BUDGET"].includes(thesis.state);
    elements.budget.disabled = state.launched;
    elements.scenario.disabled = state.launched;
  }

  function render() {
    elements.budgetOutput.textContent = `${state.budget.toFixed(1)} units`;
    elements.clock.textContent = `Round ${state.round}`;
    elements.reserve.textContent =
      `${reserve().toFixed(1)} reserve · ${state.released.toFixed(1)} released`;
    renderTrajectories();
    renderAllocation();
    renderEvidence();
    renderLineage();
    renderButtons();
  }

  function reset(announce = true) {
    state = freshState();
    elements.budget.disabled = false;
    elements.scenario.disabled = false;
    if (announce) {
      elements.status.textContent =
        "Simulation reset. Registered evidence remains unchanged below.";
    }
    render();
  }

  elements.scenario.addEventListener("change", () => reset(false));
  elements.thesis.addEventListener("change", render);
  elements.budget.addEventListener("input", () => {
    if (!state.launched) {
      state.budget = Number(elements.budget.value);
      render();
    }
  });
  elements.launch.addEventListener("click", launch);
  elements.step.addEventListener("click", advance);
  elements.kill.addEventListener("click", confirmStop);
  elements.challenge.addEventListener("click", overturnStop);
  elements.revive.addEventListener("click", revive);
  elements.reallocate.addEventListener("click", reallocate);
  elements.reset.addEventListener("click", () => reset(true));

  reset(false);
})();
