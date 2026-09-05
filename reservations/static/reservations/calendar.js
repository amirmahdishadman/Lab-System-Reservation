(() => {
  const calendarElement = document.getElementById("calendar");
  if (!calendarElement) return;
  const modalElement = document.getElementById("reservationModal");
  const modal = new bootstrap.Modal(modalElement);
  const form = document.getElementById("reservationForm");
  const errorBox = document.getElementById("reservationError");
  const deleteButton = document.getElementById("deleteReservation");
  const deleteSeriesButton = document.getElementById("deleteSeries");
  const filter = document.getElementById("systemFilter");
  const ownerField = document.getElementById("reservationOwner");
  const field = (name) => document.getElementById(name);
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const pad = (value) => String(value).padStart(2, "0");
  const wallTime = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const eventObservers = new WeakMap();

  function resizeEventText(element) {
    if (!element.classList.contains("fc-timegrid-event")) return;
    const {width, height} = element.getBoundingClientRect();
    const widthSize = width * 0.3;
    const heightSize = height < 38 ? 8 : height < 58 ? 9.5 : height < 85 ? 11 : 13;
    const fontSize = Math.max(8, Math.min(13, widthSize, heightSize));
    element.style.setProperty("--event-font-size", `${fontSize.toFixed(1)}px`);
    element.style.setProperty("--event-gap", `${Math.max(1, Math.min(5, height / 18)).toFixed(1)}px`);
    element.classList.toggle("event-block-tiny", height < 36 || width < 22);
    element.classList.toggle("event-block-compact", height < 64 || width < 42);
  }

  function observeEventSize(info) {
    if (!info.el.classList.contains("fc-timegrid-event")) return;
    resizeEventText(info.el);
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => resizeEventText(info.el));
      observer.observe(info.el);
      eventObservers.set(info.el, observer);
    }
  }

  function stopObservingEvent(info) {
    const observer = eventObservers.get(info.el);
    if (observer) observer.disconnect();
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("d-none");
  }
  function setEditable(editable) {
    ["reservationSystem", "reservationStart", "reservationEnd", "reservationPurpose", "reservationNotes", "reservationOwner", "reservationRecurrence", "reservationRecurrenceUntil"].forEach((id) => {
      const element = field(id);
      if (element) element.disabled = !editable;
    });
    form.querySelector("button[type=submit]").classList.toggle("d-none", !editable);
  }
  function updateRecurrenceVisibility() {
    const repeats = field("reservationRecurrence").value !== "none";
    field("recurrenceUntilGroup").classList.toggle("d-none", !repeats);
    field("reservationRecurrenceUntil").required = repeats;
  }
  function resetModal(start, end) {
    form.reset(); errorBox.classList.add("d-none"); setEditable(true);
    field("reservationId").value = "";
    field("reservationStart").value = wallTime(start);
    field("reservationEnd").value = wallTime(end);
    if (filter.value) field("reservationSystem").value = filter.value;
    field("reservationRecurrence").value = "none";
    field("reservationRecurrenceUntil").value = "";
    updateRecurrenceVisibility();
    deleteButton.classList.add("d-none");
    deleteSeriesButton.classList.add("d-none");
  }
  function openEvent(event) {
    const props = event.extendedProps;
    errorBox.classList.add("d-none");
    field("reservationId").value = event.id;
    field("reservationSystem").value = props.systemId;
    field("reservationStart").value = wallTime(event.start);
    field("reservationEnd").value = wallTime(event.end);
    field("reservationPurpose").value = props.purpose || "";
    field("reservationNotes").value = props.notes || "";
    if (ownerField) ownerField.value = props.ownerId;
    setEditable(props.canEdit);
    field("reservationRecurrence").value = props.recurrence || "none";
    field("reservationRecurrenceUntil").value = props.recurrenceUntil || "";
    field("reservationRecurrence").disabled = true;
    field("reservationRecurrenceUntil").disabled = true;
    updateRecurrenceVisibility();
    deleteButton.classList.toggle("d-none", !props.canEdit);
    deleteSeriesButton.classList.toggle("d-none", !props.canEdit || !props.seriesId);
    modal.show();
  }
  function payloadFromForm() {
    const payload = {
      systemId: field("reservationSystem").value,
      start: field("reservationStart").value,
      end: field("reservationEnd").value,
      purpose: field("reservationPurpose").value,
      notes: field("reservationNotes").value,
      recurrence: field("reservationRecurrence").value,
      recurrenceUntil: field("reservationRecurrenceUntil").value,
    };
    if (ownerField) payload.ownerId = ownerField.value;
    return payload;
  }
  async function request(url, method, payload) {
    const response = await fetch(url, {
      method,
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    if (!response.ok) {
      let result = {};
      try { result = await response.json(); } catch (_) { /* non-JSON response */ }
      throw new Error(result.error || "The reservation could not be saved.");
    }
    return response.status === 204 ? null : response.json();
  }
  async function updateCalendarEvent(info) {
    try {
      await request(`/api/reservations/${info.event.id}/`, "PATCH", {
        systemId: info.event.extendedProps.systemId,
        ownerId: info.event.extendedProps.ownerId,
        start: wallTime(info.event.start), end: wallTime(info.event.end),
        purpose: info.event.extendedProps.purpose, notes: info.event.extendedProps.notes,
      });
    } catch (error) { info.revert(); window.alert(error.message); }
  }

  const calendar = new FullCalendar.Calendar(calendarElement, {
    initialView: "dayGridMonth", height: "auto", nowIndicator: true,
    selectable: true, selectMirror: true, editable: true,
    slotEventOverlap: false,
    eventOrderStrict: true,
    eventMinHeight: 32,
    headerToolbar: {left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,timeGridDay"},
    events: async (info, success, failure) => {
      const params = new URLSearchParams({start: wallTime(info.start), end: wallTime(info.end)});
      if (filter.value) params.set("system", filter.value);
      try {
        const response = await fetch(`/api/reservations/?${params}`);
        if (!response.ok) throw new Error("Could not load reservations.");
        success(await response.json());
      } catch (error) { failure(error); }
    },
    select: (info) => {
      const end = info.allDay ? new Date(info.start.getTime() + 60 * 60 * 1000) : info.end;
      resetModal(info.start, end); modal.show();
    },
    eventClick: (info) => openEvent(info.event),
    eventDrop: updateCalendarEvent,
    eventResize: updateCalendarEvent,
    eventDidMount: observeEventSize,
    eventWillUnmount: stopObservingEvent,
  });
  calendar.render();

  document.getElementById("newReservation").addEventListener("click", () => {
    const start = new Date();
    start.setMinutes(Math.ceil(start.getMinutes() / 30) * 30, 0, 0);
    resetModal(start, new Date(start.getTime() + 60 * 60 * 1000)); modal.show();
  });
  filter.addEventListener("change", () => calendar.refetchEvents());
  field("reservationRecurrence").addEventListener("change", updateRecurrenceVisibility);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); errorBox.classList.add("d-none");
    const id = field("reservationId").value;
    try {
      await request(id ? `/api/reservations/${id}/` : "/api/reservations/", id ? "PATCH" : "POST", payloadFromForm());
      modal.hide(); calendar.refetchEvents();
    } catch (error) { showError(error.message); }
  });
  deleteButton.addEventListener("click", async () => {
    if (!window.confirm("Cancel this reservation?")) return;
    try {
      await request(`/api/reservations/${field("reservationId").value}/`, "DELETE", {scope: "single"});
      modal.hide(); calendar.refetchEvents();
    } catch (error) { showError(error.message); }
  });
  deleteSeriesButton.addEventListener("click", async () => {
    if (!window.confirm("Cancel this occurrence and every following occurrence in the series?")) return;
    try {
      await request(`/api/reservations/${field("reservationId").value}/`, "DELETE", {scope: "future"});
      modal.hide(); calendar.refetchEvents();
    } catch (error) { showError(error.message); }
  });
})();
