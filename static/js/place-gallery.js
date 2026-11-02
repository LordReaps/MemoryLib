(function () {
  const items = Array.from(document.querySelectorAll(".photo-grid__item"));
  const lightbox = document.getElementById("lightbox");
  if (!items.length || !lightbox) {
    return;
  }

  const image = document.getElementById("lightbox-img");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");
  const closeButton = document.getElementById("lightbox-close");
  const originals = items.map(function (item) {
    return item.getAttribute("href");
  });

  let index = 0;
  let touchStartX = null;

  function showPhoto() {
    image.src = originals[index];
    const multiple = originals.length > 1;
    prevButton.hidden = !multiple;
    nextButton.hidden = !multiple;
  }

  function openLightbox(photoIndex) {
    index = photoIndex;
    showPhoto();
    lightbox.hidden = false;
    document.body.classList.add("lightbox-open");
  }

  function closeLightbox() {
    lightbox.hidden = true;
    image.removeAttribute("src");
    document.body.classList.remove("lightbox-open");
  }

  function move(delta) {
    if (originals.length < 2) {
      return;
    }
    index = (index + delta + originals.length) % originals.length;
    showPhoto();
  }

  items.forEach(function (item, itemIndex) {
    item.addEventListener("click", function (event) {
      event.preventDefault();
      openLightbox(itemIndex);
    });
  });

  closeButton.addEventListener("click", closeLightbox);
  prevButton.addEventListener("click", function () {
    move(-1);
  });
  nextButton.addEventListener("click", function () {
    move(1);
  });

  lightbox.addEventListener("click", function (event) {
    if (event.target === lightbox) {
      closeLightbox();
    }
  });

  lightbox.addEventListener(
    "touchstart",
    function (event) {
      if (event.touches.length === 1) {
        touchStartX = event.touches[0].clientX;
      }
    },
    { passive: true }
  );

  lightbox.addEventListener(
    "touchend",
    function (event) {
      if (touchStartX === null || event.changedTouches.length !== 1) {
        return;
      }
      const delta = event.changedTouches[0].clientX - touchStartX;
      if (Math.abs(delta) > 50) {
        move(delta < 0 ? 1 : -1);
      }
      touchStartX = null;
    },
    { passive: true }
  );

  document.addEventListener("keydown", function (event) {
    if (lightbox.hidden) {
      return;
    }
    if (event.key === "Escape") {
      closeLightbox();
    } else if (event.key === "ArrowLeft") {
      move(-1);
    } else if (event.key === "ArrowRight") {
      move(1);
    }
  });
})();
