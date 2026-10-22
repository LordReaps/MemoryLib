(function () {
  const places = JSON.parse(document.getElementById("places-data").textContent);
  const createUrl = JSON.parse(document.getElementById("create-url").textContent);
  const updateUrlTemplate = JSON.parse(
    document.getElementById("update-url-template").textContent
  );
  const deleteUrlTemplate = JSON.parse(
    document.getElementById("delete-url-template").textContent
  );

  const addButton = document.getElementById("add-place-btn");
  const pickHint = document.getElementById("pick-hint");
  const form = document.getElementById("place-form");
  const formTitle = document.getElementById("place-form-title");
  const formErrors = document.getElementById("place-form-errors");
  const titleInput = document.getElementById("id_title");
  const descriptionInput = document.getElementById("id_description");
  const startDateInput = document.getElementById("id_start_date");
  const endDateInput = document.getElementById("id_end_date");
  const latitudeInput = document.getElementById("id_latitude");
  const longitudeInput = document.getElementById("id_longitude");
  const placeIdInput = document.getElementById("id_place_id");
  const coordsLabel = document.getElementById("place-coords");
  const cancelButton = document.getElementById("place-form-cancel");

  const deletePhotoUrlTemplate = JSON.parse(document.getElementById("delete-photo-url-template").textContent);

  delete L.Icon.Default.prototype._getIconUrl;

  L.Icon.Default.mergeOptions({
    iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
    iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
    shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  });

  const map = L.map("map", {
    zoomControl: true,
    minZoom: 2,
    maxBounds: [[-90, -180], [90, 180]], // ограничивает перемещение границами мира
    maxBoundsViscosity: 1.0 // не даёт «выпрыгнуть» за границы
    }).setView([50.0, 10.0], 4);

  map.attributionControl.remove();

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    minZoom: 2,
    noWrap: true,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  const markers = new Map();
  let pickingPoint = false;
  let previewMarker = null;
  let editingPlaceId = null;
  let originalEditLatLng = null;

  const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  const redIcon = L.divIcon({
    className: "custom-red-marker",
    iconSize: [24, 24],
    iconAnchor: [12, 12]
  });

  function formatDate(isoDate) {
    const [year, month, day] = isoDate.split("-").map(Number);
    return dateFormatter.format(new Date(year, month - 1, day));
  }

  function formatDateRange(startDate, endDate) {
    if (startDate && endDate) {
      if (startDate === endDate) {
        return formatDate(startDate);
      }
      return `${formatDate(startDate)} — ${formatDate(endDate)}`;
    }
    if (startDate) {
      return `с ${formatDate(startDate)}`;
    }
    if (endDate) {
      return `до ${formatDate(endDate)}`;
    }
    return "";
  }

  function formatCoords(lat, lng) {
    return `${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)}`;
  }

  function setCoords(lat, lng) {
    latitudeInput.value = lat;
    longitudeInput.value = lng;
    coordsLabel.textContent = `Координаты: ${formatCoords(lat, lng)}`;
  }

  function showErrors(errors) {
    const messages = [];
    Object.keys(errors).forEach(function (field) {
      errors[field].forEach(function (item) {
        messages.push(item.message || item);
      });
    });
    formErrors.textContent = messages.join(" ");
    formErrors.hidden = messages.length === 0;
  }

  function clearErrors() {
    formErrors.textContent = "";
    formErrors.hidden = true;
  }

  function urlFor(template, id) {
    return template.replace("{pk}", String(id));
  }

  function buildPopupContent(place) {
    const wrap = document.createElement("div");
    wrap.className = "place-popup";

    const title = document.createElement("h2");
    title.className = "place-popup__title";
    title.textContent = place.title;
    wrap.appendChild(title);

    const datesText = formatDateRange(place.start_date, place.end_date);
    if (datesText) {
      const dates = document.createElement("p");
      dates.className = "place-popup__dates";
      dates.textContent = datesText;
      wrap.appendChild(dates);
    }

    const description = (place.description || "").trim();
    if (description) {
      const descriptionEl = document.createElement("p");
      descriptionEl.className = "place-popup__description";
      descriptionEl.textContent = description;
      wrap.appendChild(descriptionEl);
    }

    if (place.photos && place.photos.length > 0) {
      const gallery = document.createElement("div");
      gallery.className = "place-popup__gallery";

      place.photos.forEach(function(photo) {
        const imgWrap = document.createElement("div");
        imgWrap.className = "place-popup__thumb-wrap";

        const img = document.createElement("img");
        img.src = photo.thumb_url || photo.original_url;
        img.className = "place-popup__thumb";

        img.addEventListener("click", function() {
          const lb = document.getElementById("lightbox");
          document.getElementById("lightbox-img").src = photo.original_url;
          lb.hidden = false;
        });

        imgWrap.appendChild(img);
        gallery.appendChild(imgWrap);
      });

      wrap.appendChild(gallery);
    }

    const actions = document.createElement("div");
    actions.className = "place-popup__actions";

    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "btn btn--ghost";
    editButton.textContent = "Изменить";
    editButton.addEventListener("click", function () {
      startEdit(place.id);
    });

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "btn btn--danger";
    deleteButton.textContent = "Удалить";
    deleteButton.addEventListener("click", function () {
      confirmDelete(place);
    });

    actions.appendChild(editButton);
    actions.appendChild(deleteButton);
    wrap.appendChild(actions);
    return wrap;
  }

  function bindPopup(marker, place) {
    marker.bindPopup(buildPopupContent(place), {
      className: "place-popup-wrap",
      closeButton: true,
      autoPan: true,
      maxWidth: 280,
      offset: [0, -8],
    });
  }

  function addPlaceMarker(place, openPopup) {
    const marker = L.marker([place.latitude, place.longitude], {
    draggable: false,
    icon: redIcon // <-- добавляем иконку
    }).addTo(map);

    marker.placeData = place;
    bindPopup(marker, place);
    marker.on("click", function (event) {
      if (pickingPoint) {
        L.DomEvent.stop(event);
        map.closePopup();
      }
    });
    markers.set(place.id, marker);
    if (openPopup) {
      marker.openPopup();
    }
    return marker;
  }

  function refreshMarker(place, openPopup) {
    const marker = markers.get(place.id);
    if (!marker) {
      addPlaceMarker(place, openPopup);
      return;
    }
    marker.placeData = place;
    marker.setLatLng([place.latitude, place.longitude]);
    marker.dragging.disable();
    marker.off("dragend");
    bindPopup(marker, place);
    if (openPopup) {
      marker.openPopup();
    }
  }

  function removePreviewMarker() {
    if (previewMarker) {
      map.removeLayer(previewMarker);
      previewMarker = null;
    }
  }

  function placePreviewAt(latlng) {
    if (!previewMarker) {
      previewMarker = L.marker(latlng, { draggable: true, icon: redIcon }).addTo(map);
      previewMarker.on("dragend", function () {
        const position = previewMarker.getLatLng();
        setCoords(position.lat, position.lng);
      });
    } else {
      previewMarker.setLatLng(latlng);
    }
    setCoords(latlng.lat, latlng.lng);
  }

  function resetForm() {
    form.reset();
    placeIdInput.value = "";
    latitudeInput.value = "";
    longitudeInput.value = "";
    coordsLabel.textContent = "";
    clearErrors();
  }

  function closeForm() {
    form.hidden = true;
    pickingPoint = false;
    pickHint.hidden = true;
    addButton.hidden = false;
    editingPlaceId = null;
    originalEditLatLng = null;
    removePreviewMarker();
    markers.forEach(function (marker) {
      marker.dragging.disable();
    });
  }

  function openCreateForm() {
    formTitle.textContent = "Новое место";
    form.hidden = false;
    addButton.hidden = true;
    pickHint.hidden = true;
    pickingPoint = false;
    titleInput.focus();
  }

  function startCreate() {
    closeForm();
    resetForm();
    map.closePopup();
    pickingPoint = true;
    pickHint.textContent =
      "Нажмите на карту, чтобы выбрать точку. Затем можно сдвинуть флажок.";
    pickHint.hidden = false;
    addButton.hidden = true;
  }

  function startEdit(placeId) {
    const marker = markers.get(placeId);
    if (!marker) {
      return;
    }
    const place = marker.placeData;
    closeForm();
    resetForm();
    map.closePopup();

    editingPlaceId = placeId;
    originalEditLatLng = marker.getLatLng();
    placeIdInput.value = String(place.id);
    titleInput.value = place.title;
    descriptionInput.value = place.description || "";
    startDateInput.value = place.start_date || "";
    endDateInput.value = place.end_date || "";
    setCoords(place.latitude, place.longitude);

    renderPreviews(place.photos);

    marker.off("dragend");
    marker.dragging.enable();
    marker.on("dragend", function () {
      const position = marker.getLatLng();
      setCoords(position.lat, position.lng);
    });

    formTitle.textContent = "Редактирование";
    form.hidden = false;
    addButton.hidden = true;
    pickingPoint = true;
    pickHint.hidden = false;
    pickHint.textContent = "Перетащите флажок или нажмите новое место на карте.";
    titleInput.focus();
  }

  function restoreEditedMarker() {
    if (editingPlaceId == null || !originalEditLatLng) {
      return;
    }
    const marker = markers.get(editingPlaceId);
    if (marker) {
      marker.setLatLng(originalEditLatLng);
      marker.dragging.disable();
    }
  }

  function confirmDelete(place) {
    const confirmed = window.confirm(`Удалить место «${place.title}»?`);
    if (!confirmed) {
      return;
    }
    const body = new FormData();
    const csrf = form.querySelector("[name=csrfmiddlewaretoken]");
    body.append("csrfmiddlewaretoken", csrf.value);

    fetch(urlFor(deleteUrlTemplate, place.id), {
      method: "POST",
      body: body,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Не удалось удалить место");
        }
        return response.json();
      })
      .then(function () {
        const marker = markers.get(place.id);
        if (marker) {
          map.removeLayer(marker);
          markers.delete(place.id);
        }
        if (editingPlaceId === place.id) {
          closeForm();
        }
      })
      .catch(function (error) {
        window.alert(error.message);
      });
  }

  map.on("click", function (event) {
    if (!pickingPoint) {
      return;
    }
    if (editingPlaceId != null) {
      const marker = markers.get(editingPlaceId);
      if (marker) {
        marker.setLatLng(event.latlng);
        setCoords(event.latlng.lat, event.latlng.lng);
      }
      return;
    }
    placePreviewAt(event.latlng);
    if (form.hidden) {
      openCreateForm();
    }
  });

  addButton.addEventListener("click", startCreate);
  cancelButton.addEventListener("click", function () {
    restoreEditedMarker();
    closeForm();
    resetForm();
    pickHint.textContent =
      "Нажмите на карту, чтобы выбрать точку. Затем можно сдвинуть флажок.";
  });

    const photosInput = document.getElementById("id_photos");
  const photoPreview = document.getElementById("photo-preview");
  let previewUrls = [];
  let selectedFiles = new DataTransfer(); // Файлы, которые ждут отправки

  // Функция отрисовки одного превью с крестиком
  function createPreviewItem(src, onRemove) {
    const wrap = document.createElement("div");
    wrap.className = "photo-preview__wrap";

    const img = document.createElement("img");
    img.src = src;
    img.className = "photo-preview__img";

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "photo-preview__del";
    delBtn.innerHTML = "&times;";
    delBtn.addEventListener("click", onRemove);

    wrap.appendChild(img);
    wrap.appendChild(delBtn);
    return wrap;
  }

  function renderPreviews(existingPhotos) {
    clearPreviews();

    // 1. Рисуем фото из базы (при редактировании)
    if (existingPhotos) {
      existingPhotos.forEach(function(photo) {
        const item = createPreviewItem(photo.thumb_url || photo.original_url, function() {
          // Сценарий 2: Удаление сохраненного фото
          if (!confirm("Удалить это фото из базы?")) return;

          fetch(urlFor(deletePhotoUrlTemplate, photo.id), {
            method: "POST",
            headers: {
              "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value
            }
          }).then(r => r.json()).then(data => {
            if (data.ok) {
              // Удаляем из памяти текущего места, чтобы при сохранении формы оно не вернулось
              const marker = markers.get(data.place_id);
              if (marker && marker.placeData && marker.placeData.photos) {
                marker.placeData.photos = marker.placeData.photos.filter(p => p.id !== photo.id);
              }
              // Перерисовываем превью заново
              renderPreviews(marker ? marker.placeData.photos : []);
            }
          });
        });
        photoPreview.appendChild(item);
      });
    }

    // 2. Рисуем только что выбранные файлы
    Array.from(selectedFiles.files).forEach(function(file, index) {
      const url = URL.createObjectURL(file);
      previewUrls.push(url);

      const item = createPreviewItem(url, function() {
        // Сценарий 1: Удаление только что прикрепленного файла
        const newTransfer = new DataTransfer();
        // Копируем все файлы, КРОМЕ удаляемого
        Array.from(selectedFiles.files).forEach((f, i) => {
          if (i !== index) newTransfer.items.add(f);
        });
        selectedFiles = newTransfer;
        photosInput.files = selectedFiles.files; // Обновляем инпут для формы
        URL.revokeObjectURL(url); // Освобождаем память

        // Перерисовываем
        const currentPlace = placeIdInput.value ? markers.get(Number(placeIdInput.value)).placeData : null;
        renderPreviews(currentPlace ? currentPlace.photos : null);
      });

      photoPreview.appendChild(item);
    });
  }

  function clearPreviews() {
    previewUrls.forEach(function(url) { URL.revokeObjectURL(url); });
    previewUrls = [];
    photoPreview.innerHTML = "";
  }

  // Слушаем выбор новых файлов
  photosInput.addEventListener("change", function (e) {
    for (let i = 0; i < e.target.files.length; i++) {
      selectedFiles.items.add(e.target.files[i]);
    }
    photosInput.files = selectedFiles.files;
    const currentPlace = placeIdInput.value ? markers.get(Number(placeIdInput.value)).placeData : null;
    renderPreviews(currentPlace ? currentPlace.photos : null);
  });

  // Переопределяем resetForm
  const originalResetForm = resetForm;
  resetForm = function() {
    originalResetForm();
    selectedFiles = new DataTransfer();
    photosInput.value = "";
    clearPreviews();
  };

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearErrors();

    const isEdit = Boolean(placeIdInput.value);
    const url = isEdit
      ? urlFor(updateUrlTemplate, placeIdInput.value)
      : createUrl;

    fetch(url, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          showErrors(result.data.errors || { __all__: [{ message: "Ошибка сохранения" }] });
          return;
        }
        const place = result.data.place;
        if (isEdit) {
          refreshMarker(place, true);
        } else {
          addPlaceMarker(place, true);
        }
        pickHint.textContent =
          "Нажмите на карту, чтобы выбрать точку. Затем можно сдвинуть флажок.";
        closeForm();
        resetForm();
      })
      .catch(function () {
        showErrors({ __all__: [{ message: "Не удалось сохранить место" }] });
      });
  });

  // Логика закрытия лайтбокса
  const lightbox = document.getElementById("lightbox");
  const lightboxClose = document.getElementById("lightbox-close");

  function closeLightbox() {
    lightbox.hidden = true;
    document.getElementById("lightbox-img").src = ""; // Очищаем, чтобы не грузить впустую
  }

  lightboxClose.addEventListener("click", closeLightbox);
  lightbox.addEventListener("click", function (e) {
    // Закрываем, если кликнули по фону, а не по самому фото
    if (e.target === lightbox) {
      closeLightbox();
    }
  });

  places.forEach(function (place) {
    addPlaceMarker(place, false);
  });

  const markerList = Array.from(markers.values());
  if (markerList.length === 1) {
    map.setView(markerList[0].getLatLng(), 10);
  } else if (markerList.length > 1) {
    const bounds = L.latLngBounds(
      markerList.map(function (marker) {
        return marker.getLatLng();
      })
    );
    map.fitBounds(bounds, { padding: [48, 48], maxZoom: 12 });
  }
})();
