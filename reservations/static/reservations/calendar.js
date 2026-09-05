(() => {
  const calendarElement = document.getElementById("calendar");
  if (!calendarElement) return;
  const modalElement = document.getElementById("reservationModal");
  const modal = new bootstrap.Modal(modalElement);
  const form = document.getElementById("reservationForm");
  const errorBox = document.getElementById("reservationError");
  const deleteButton = document.getElementById("deleteReservation");
  const filter = document.getElementById("systemFilter");
  const ownerField = document.getElementById("reservationOwner");
  const field = (name) => document.getElementById(name);
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const pad = (value) => String(value).padStart(2, "0");
  const wallTime = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("d-none");
  }
  function setEditable(editable) {
    ["reservationSystem", "reservationStart", "reservationEnd", "reservationPurpose", "reservationNotes", "reservationOwner"].forEach((id) => {
      const element = field(id);
      if (element) element.disabled = !editable;
    });
    form.querySelector("button[type=submit]").classList.toggle("d-none", !editable);
  }
  function resetModal(start, end) {
    form.reset(); errorBox.classList.add("d-none"); setEditable(true);
    field("reservationId").value = "";
    field("reservationStart").value = wallTime(start);
    field("reservationEnd").value = wallTime(end);
    if (filter.value) field("reservationSystem").value = filter.value;
    deleteButton.classList.add("d-none");
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
    deleteButton.classList.toggle("d-none", !props.canEdit);
    modal.show();
  }
  function payloadFromForm() {
    const payload = {
      systemId: field("reservationSystem").value,
      start: field("reservationStart").value,
      end: field("reservationEnd").value,
      purpose: field("reservationPurpose").value,
      notes: field("reservationNotes").value,
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
  });
  calendar.render();

  document.getElementById("newReservation").addEventListener("click", () => {
    const start = new Date();
    start.setMinutes(Math.ceil(start.getMinutes() / 30) * 30, 0, 0);
    resetModal(start, new Date(start.getTime() + 60 * 60 * 1000)); modal.show();
  });
  filter.addEventListener("change", () => calendar.refetchEvents());
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
      await request(`/api/reservations/${field("reservationId").value}/`, "DELETE");
      modal.hide(); calendar.refetchEvents();
    } catch (error) { showError(error.message); }
  });
})();
