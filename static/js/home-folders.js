(function () {
  const button = document.getElementById("create-folder-btn");
  if (!button) {
    return;
  }

  const createUrlNode = document.getElementById("create-folder-url");
  const folderUrlNode = document.getElementById("folder-url-template");
  if (!createUrlNode || !folderUrlNode) {
    return;
  }

  const createUrl = JSON.parse(createUrlNode.textContent);
  const folderUrlTemplate = JSON.parse(folderUrlNode.textContent);

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[2]) : "";
  }

  button.addEventListener("click", function () {
    const entered = window.prompt("Название новой папки");
    const name = (entered || "").trim();
    if (!name) {
      return;
    }

    const body = new FormData();
    body.append("name", name);
    body.append("csrfmiddlewaretoken", getCookie("csrftoken"));

    fetch(createUrl, { method: "POST", body: body })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        if (!data.ok) {
          const errors = data.errors && data.errors.name;
          throw new Error((errors && errors[0]) || "Не удалось создать папку");
        }
        window.location.href = folderUrlTemplate.replace(
          "{pk}",
          String(data.folder.id)
        );
      })
      .catch(function (error) {
        window.alert(error.message || "Не удалось создать папку");
      });
  });
})();
