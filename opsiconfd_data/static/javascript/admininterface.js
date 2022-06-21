function createUUID() {
	if (typeof crypto.randomUUID === "function") {
		return crypto.randomUUID();
	}
	return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
		var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
		return v.toString(16);
	});
}


function monitorSession() {
	if (document.cookie && document.cookie.indexOf('opsiconfd-session=') != -1) {
		setTimeout(monitorSession, 1000);
	}
	else {
		console.info('Session expired')
		location.href = "/login";
	}
}


function unblockAll() {
	let req = doReq("POST", "/admin/unblock-all");
	req.then((result) => {
		outputToHTML(result, "json-result");
		outputResult(result, "text-result");
		loadClientTable()
		return result
	});
}


function unblockClient(ip) {
	if (ValidateIPaddress(ip)) {
		let req = doReq("POST", "/admin/unblock-client", { "client_addr": ip });
		req.then((result) => {
			outputToHTML(result, "json-result");
			outputResult(result, "text-result");
			loadClientTable()
			return result
		});
	}
}


function clearRedisCache(depots = []) {
	let req = doReq("POST", "/redis-interface/clear-product-cache", { "depots": depots });
	req.then((result) => {
		outputToHTML(result, "redis-result");
		return result
	});
}


function loadClientTable() {
	let req = doReq("GET", "/admin/blocked-clients");
	req.then((result) => {
		printClientTable(result, "blocked-clients-div");
		return result
	});
}


function loadLockedProductsTable() {
	let req = doReq("GET", "/admin/locked-products-list");
	req.then((result) => {
		printLockedProductsTable(result, "locked-products-table-div");
		return result
	});
}


function unlockProduct(product) {
	let req = doReq("POST", "/admin/products/" + product + "/unlock");
	req.then((result) => {
		loadLockedProductsTable();
		return result
	});
}


function unlockAllProducts() {
	let req = doReq("POST", "/admin/products/unlock");
	req.then((result) => {
		loadLockedProductsTable();
		return result
	});
}


function loadRedisInfo() {
	let req = doReq("GET", "/redis-interface/redis-stats");
	req.then((result) => {
		outputToHTML(result, "redis-result");
		return result
	});
}


function loadSessionTable() {
	let req = doReq("GET", "/admin/session-list");
	req.then((result) => {
		printSessionTable(result, "session-table-div");
		return result
	});
}

function loadRPCTable(sortKey, sort) {
	let req = doReq("GET", "/admin/rpc-list");
	req.then((result) => {
		if (result.length == 0) {
			document.getElementById("rpc-table-div").innerHTML = "No rpcs found.";
			return null
		}
		if (sort) {
			result = sortRPCTable(result, sortKey);
		}
		printRPCTable(result, "rpc-table-div");
		return result;
	});
}


function loadAddons() {
	let req = doReq("GET", "/admin/addons");
	req.then((result) => {
		printAddonTable(result, "addon-table-div");
		return result
	});
}


function installAddon() {
	let button = null;
	if (window.event.currentTarget && window.event.currentTarget.tagName.toLowerCase() == "button") {
		button = window.event.currentTarget;
		button.classList.add("loading");
	}
	let formData = new FormData();
	formData.append("addonfile", document.getElementById("addon-file").files[0]);

	let req = doReq("POST", "/admin/addons/install", formData, handleError = false);
	req.then((result) => {
		console.log(result);
		if (button) {
			button.classList.remove("loading");
		}
		loadAddons();
		document.getElementById("alerts").innerHTML += "<div class='success'> <span class=\"closebtn\" onclick=\"this.parentNode.remove()\">&times;</span>Addon installed</div>"
	}, (error) => {
		if (button) {
			button.classList.remove("loading");
		}
		console.log(error);
		console.warn(error.status, error.details);
		document.getElementById("alerts").innerHTML += "<div class='alert'> <span class=\"closebtn\" onclick=\"this.parentNode.remove()\">&times;</span>" + error.message + "</div>"
	});
}


function deleteClientSessions() {
	body = {
		"client_addr": sessionAddr.value
	};
	if (ValidateIPaddress(sessionAddr.value)) {
		let req = doReq("POST", "/admin/delete-client-sessions", body);
		req.then((result) => {
			outputToHTML(result, "json-result");
			outputResult(result, "text-result");
			return result

		}, (error) => {
			console.log(error);
		});
	}
}


function loadInfo() {
	let request1 = new XMLHttpRequest();
	let config_req = doReq("GET", "/admin/config");
	config_req.then((result) => {
		outputToHTML(result, "config-values");
		return result;
	});
	let routes_req = doReq("GET", "/admin/routes");
	routes_req.then((result) => {
		outputToHTML(result, "route-values");
		return result;
	});
}


function reload() {
	let req = doReq("POST", "/admin/reload");
	req.then((result) => {
		console.debug(result);
		return result
	});
}


function logout() {
	let req = doReq("POST", "/admin/logout");
	req.then(() => {
		location.href = "/login";
	});
}

function callRedis() {
	let req = doReq("POST", "/redis-interface", { "cmd": document.getElementById("redis-cmd").value }, handleError = false);
	req.then((result) => {
		console.debug(`Redis command successful: ${JSON.stringify(result)}`)
		outputToHTML(result, "redis-result");
	}, (error) => {
		console.error(error);
		outputToHTML(error, "redis-result");
	});
}


function callJSONRPC() {
	let inputs = document.getElementById("tab-rpc-interface").getElementsByTagName("input");
	for (i = 0; i < inputs.length; i++) {
		let name = inputs[i].name.trim();
		let value = inputs[i].value.trim();

		if (!value && name.substring(0, 1) != "*") {

			alert("mandatory field empty");
			return {
				"error": "mandatory field empty"
			};
		}
	}

	let apiJSON = createRequestJSON();
	let req = doReq("POST", "/rpc", apiJSON, true, true);
	req.then((result) => {
		document.getElementById("jsonrpc-execute-button").disabled = false;
		document.getElementById("jsonrpc-response-info").innerHTML = "Server-Timing: " + result.requestInfo.serverTiming// request.getResponseHeader("server-timing")
		outputToHTML(result.data, "jsonrpc-result");
		return result;
	}).finally(() => {
		document.getElementById("jsonrpc-execute-button").disabled = true;
	});
}


function loadLicensingInfo() {
	let req = doReq("GET", "/admin/licensing_info");
	req.then(() => {
		console.log("Licensing info:");
		console.log(result);
		if (typeof result.module_dates != "undefined" && Object.keys(result.module_dates).length > 0) {
			generateLiceningInfoTable(result.info, "licensing-info");
			generateLiceningDatesTable(result.module_dates, result.active_date, "licensing-dates");
		} else {
			div = document.getElementById("licensing-info").innerHTML = "<p>No licenses available.</p>";
			div = document.getElementById("licensing-dates").innerHTML = "";
		}
	});
}


function licenseUpload(files) {
	var formData = new FormData();
	for (var i = 0; i < files.length; i++) {
		formData.append("files", files[i]);
	}
	let req = doReq("POST", "/admin/license_upload", formData);
	req.then((result) => {
		console.log(`File upload successful: ${JSON.stringify(result)}`)
		loadLicensingInfo();
	});
}


// function licenseUpload(files) {
// 	var formData = new FormData();
// 	for (var i = 0; i < files.length; i++) {
// 		formData.append("files", files[i]);
// 	}
// 	const xhr = new XMLHttpRequest();
// 	xhr.responseType = 'json';
// 	xhr.open("POST", "/admin/license_upload", true);
// 	xhr.onload = function (e) {
// 		if (this.status == 201) {
// 			console.log(`File upload successful: ${JSON.stringify(this.response)}`)
// 			loadLicensingInfo();
// 		}
// 		else {
// 			let error = `File upload failed: ${JSON.stringify(this.response)}`;
// 			console.error(error);
// 			alert(error);
// 		}
// 	};
// 	xhr.send(formData);
// }


// function loadLicensingInfo() {
// 	let xhr = new XMLHttpRequest();
// 	xhr.open("GET", "/admin/licensing_info");
// 	xhr.responseType = 'json';
// 	xhr.onload = function (e) {
// 		if (this.status == 200) {
// 			console.log("Licensing info:");
// 			console.log(this.response);
// 			if (Object.keys(this.response.data.module_dates).length > 0) {
// 				generateLiceningInfoTable(this.response.data.info, "licensing-info");
// 				generateLiceningDatesTable(this.response.data.module_dates, this.response.data.active_date, "licensing-dates");
// 			} else {
// 				div = document.getElementById("licensing-info").innerHTML = "<p>No licenses available.</p>";
// 				div = document.getElementById("licensing-dates").innerHTML = "";
// 			}
// 		}
// 		else {
// 			console.error(this.response.error);
// 		}
// 	};
// 	xhr.send();
// }

// function callJSONRPC() {
// 	let inputs = document.getElementById("tab-rpc-interface").getElementsByTagName("input");
// 	for (i = 0; i < inputs.length; i++) {
// 		let name = inputs[i].name.trim();
// 		let value = inputs[i].value.trim();

// 		if (!value && name.substr(0, 1) != "*") {

// 			alert("mandatory field empty");
// 			return {
// 				"error": "mandatory field empty"
// 			};
// 		}
// 	}

// 	let apiJSON = createRequestJSON();
// 	let request = new XMLHttpRequest();
// 	request.open("POST", "/rpc");

// 	request.addEventListener('load', function (event) {
// 		document.getElementById("jsonrpc-execute-button").disabled = false;
// 		document.getElementById("jsonrpc-response-info").innerHTML = "Server-Timing: " + request.getResponseHeader("server-timing");
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText
// 			result = JSON.parse(result);
// 			outputToHTML(result, "jsonrpc-result");
// 			return result;

// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	document.getElementById("jsonrpc-execute-button").disabled = true;
// 	request.send(JSON.stringify(apiJSON));
// }

// function unlockAllProducts() {
// 	let request = new XMLHttpRequest();
// 	request.open("POST", "/admin/products/unlock");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText
// 			result = JSON.parse(result);
// 			loadLockedProductsTable()
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	request.send();

// }


// function callRedis() {
// 	let xhr = new XMLHttpRequest();
// 	xhr.open("POST", "/redis-interface");
// 	xhr.responseType = 'json';
// 	xhr.onload = function (e) {
// 		if (this.status == 200) {
// 			console.log(`Redis command successful: ${JSON.stringify(this.response)}`)
// 			outputToHTML(this.response, "redis-result");
// 		}
// 		else {
// 			console.error(this.response.error);
// 			outputToHTML(this.response, "redis-result");
// 		}
// 	};
// 	xhr.send(JSON.stringify());
// }

// function reload() {
// 	let request = new XMLHttpRequest();
// 	request.open("POST", "/admin/reload");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			console.debug(request.statusText, request.responseText);
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 		}
// 	});
// 	request.send();
// }

// function logout() {
// 	let request = new XMLHttpRequest();
// 	request.open("GET", "/session/logout");
// 	request.addEventListener('load', function (event) {
// 		location.href = "/login";
// 	});
// 	request.send();
// }

// function loadInfo() {
// 	let request1 = new XMLHttpRequest();
// 	request1.open("GET", "/admin/config");
// 	request1.addEventListener('load', function (event) {
// 		if (request1.status >= 200 && request1.status < 300) {
// 			result = request1.responseText;
// 			result = JSON.parse(result);
// 			outputToHTML(result.data, "config-values");
// 			return result;
// 		} else {
// 			console.warn(request1.statusText, request1.responseText);
// 			return request1.statusText;
// 		}
// 	});
// 	request1.send()

// 	request2 = new XMLHttpRequest();
// 	request2.open("GET", "/admin/routes");
// 	request2.addEventListener('load', function (event) {
// 		if (request2.status >= 200 && request2.status < 300) {
// 			result = request2.responseText;
// 			result = JSON.parse(result);
// 			outputToHTML(result.data, "route-values");
// 			return result;
// 		} else {
// 			console.warn(request2.statusText, request2.responseText);
// 			return request2.statusText;
// 		}
// 	});
// 	request2.send()
// }

// function deleteClientSessions() {
// 	let request = new XMLHttpRequest();
// 	request.open("POST", "/admin/delete-client-sessions");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText
// 			result = JSON.parse(result);
// 			outputToHTML(result, "json-result");
// 			outputResult(result, "text-result");
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	body = {
// 		"client_addr": sessionAddr.value
// 	};
// 	if (ValidateIPaddress(sessionAddr.value)) {
// 		request.send(JSON.stringify(body));
// 	}

// }

// function installAddon() {
// 	let button = null;
// 	if (window.event.currentTarget && window.event.currentTarget.tagName.toLowerCase() == "button") {
// 		button = window.event.currentTarget;
// 		button.classList.add("loading");
// 	}
// 	let formData = new FormData();
// 	formData.append("addonfile", document.getElementById("addon-file").files[0]);
// 	console.log(formData);
// 	var request = new XMLHttpRequest();
// 	request.open("POST", "/admin/addons/install");
// 	request.addEventListener('load', function (event) {
// 		if (button) {
// 			button.classList.remove("loading");
// 		}
// 		if (request.status >= 200 && request.status < 300) {
// 			loadAddons();
// 			document.getElementById("alerts").innerHTML += "<div class='success'> <span class=\"closebtn\" onclick=\"this.parentNode.remove()\">&times;</span>Addon installed</div>"
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			document.getElementById("alerts").innerHTML += "<div class='alert'> <span class=\"closebtn\" onclick=\"this.parentNode.remove()\">&times;</span>" + request.responseText + "</div>"
// 		}
// 	});
// 	request.send(formData);
// }

// function loadAddons() {
// 	let request = new XMLHttpRequest();
// 	request.open("GET", "/admin/addons");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText;
// 			result = JSON.parse(result);
// 			printAddonTable(result, "addon-table-div");
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 		}
// 	});
// 	request.send()
// }

// function loadRPCTable(sortKey, sort) {
// 	let request = new XMLHttpRequest();
// 	request.open("GET", "/admin/rpc-list");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText;
// 			result = JSON.parse(result);
// 			if (result.length == 0) {
// 				document.getElementById("rpc-table-div").innerHTML = "No rpcs found.";
// 				return null
// 			}
// 			if (sort) {
// 				result = sortRPCTable(result, sortKey);
// 			}
// 			printRPCTable(result, "rpc-table-div");
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	request.send();
// 	let request_count = new XMLHttpRequest();
// 	request_count.open("GET", "/admin/rpc-count");
// 	request_count.addEventListener('load', function (event) {
// 		if (request_count.status >= 200 && request_count.status < 300) {
// 			result = request_count.responseText;
// 			result = JSON.parse(result);
// 			date = new Date(result["date_first_rpc"])
// 			// printRPCCount(result["rpc_count"], date)
// 			return result;
// 		} else {
// 			console.warn(request_count.statusText, request_count.responseText);
// 			return request_count.statusText;
// 		}
// 	});
// 	request_count.send();
// }

// function loadSessionTable(sortKey, sort) {
// 	let request = new XMLHttpRequest();
// 	request.open("GET", "/admin/session-list");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText;
// 			result = JSON.parse(result);
// 			printSessionTable(result, "session-table-div");
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 		}
// 	});
// 	request.send();
// }

// function loadRedisInfo() {
// 	let request = new XMLHttpRequest();
// 	request.open("GET", "/redis-interface/redis-stats");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText;
// 			result = JSON.parse(result);
// 			outputToHTML(result, "redis-result");
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	request.send()
// }


// function unlockProduct(product) {
// 	let request = new XMLHttpRequest();
// 	request.open("POST", "/admin/products/" + product + "/unlock");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText
// 			result = JSON.parse(result);
// 			loadLockedProductsTable()
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	request.send();

// }


// function loadClientTable() {
// 	let request = new XMLHttpRequest();
// 	request.open("GET", "/admin/blocked-clients");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText;
// 			result = JSON.parse(result);
// 			printClientTable(result, "blocked-clients-div");
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	request.send()
// }

// function clearRedisCache(depots = []) {
// 	let request = new XMLHttpRequest();
// 	request.open("POST", "/redis-interface/clear-product-cache");
// 	request.addEventListener('load', function (event) {
// 		if (request.status >= 200 && request.status < 300) {
// 			result = request.responseText
// 			result = JSON.parse(result);
// 			outputToHTML(result, "redis-result");
// 			return result;
// 		} else {
// 			console.warn(request.statusText, request.responseText);
// 			return request.statusText;
// 		}
// 	});
// 	body = {
// 		"depots": depots
// 	};

// 	request.send(JSON.stringify(body));

// }



// function deleteClientSessions() {
// 	let req = doReq("POST", "/admin/delete-client-sessions");
// 	req.then((result) => {
// 		outputToHTML(result.data, "json-result");
// 		outputResult(result.data, "text-result");
// 		loadClientTable()
// 		return result
// 	}).catch((error) => {
// 		console.warn(result.status, result.data);
// 		return result.status;
// 	});
// }




function outputResult(json, id) {
	let text = "";
	if (json["status"] == 200) {
		data = json["data"]
		let failedCount = 0;
		let blockedCount = 0;
		if (data["redis-keys"] != undefined) {
			data["redis-keys"].forEach(element => {
				// console.log(element);
				if (element.includes("failed_auth")) {
					failedCount += 1;
				}
				else {
					blockedCount += 1;
				}
			});
		}
		if (data["clients"] != undefined && data["clients"].length != 0) {
			if (blockedCount == 0) {
				text = "No blocked clients found."
			}
			else if (blockedCount == 1) {
				text = blockedCount + " client unblocked.";
			} else {
				text = blockedCount + " clients unblocked.";
			}
			if (failedCount == 1) {
				text = text + " Failed logins for " + failedCount + " client deleted.";
			} else {
				text = text + " Failed logins for " + failedCount + " clients deleted.";
			}

		} else if (data["sessions"] != undefined) {
			if (data["sessions"] != 0) {
				text = "All sessions from client " + data["client"] + " deleted.";
			} else {
				text = "No sessions on client found.";
			}
		} else if (data["client"] != undefined && data["client"].length != 0) {
			text = "Client with address " + data["client"] + " unblocked.";
		} else {
			text = "No blocked clients found.";
		}
	} else {
		text = "Error while unblocking clients.";
	}
	document.getElementById(id).style.visibility = 'visible';
	document.getElementById(id).innerHTML = text;
}



// https://stackoverflow.com/questions/4810841/pretty-print-json-using-javascript
function syntaxHighlight(json) {
	if (typeof json != 'string') {
		json = JSON.stringify(json, undefined, 2);
	}
	json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
	return json.replace(
		/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
		function (match) {
			var cls = 'json_number';
			if (/^"/.test(match)) {
				if (/:$/.test(match)) {
					cls = 'json_key';
				} else {
					cls = 'json_string';
				}
			} else if (/true|false/.test(match)) {
				cls = 'json_boolean';
			} else if (/null/.test(match)) {
				cls = 'json_null';
			}
			return '<span class="' + cls + '">' + match + '</span>';
		});
}

function ValidateIPaddress(ipaddress) {
	if (/^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/
		.test(ipaddress)) {
		return (true)
	}
	alert("You have entered an invalid IP address!")
	return (false)
}


function printSessionTable(data, htmlId) {
	if (data.length == 0) {
		htmlStr = "<p>No sessions found.</p>";
	} else {
		data.sort((a, b) => (a.session_id > b.session_id) ? 1 : -1);
		htmlStr = "<table class=\"session-table\" id=\"session-table\">" +
			"<tr>" +
			"<th class='session-th'>Address</th>" +
			"<th class='session-th'>Session ID</th>" +
			"<th class='session-th'>User-Agent</th>" +
			"<th class='session-th'>Username</th>" +
			"<th class='session-th'>Validity</th>" +
			"</tr>";
		data.forEach(element => {
			htmlStr += "<tr>" +
				"<td class=\"session-td\">" + element.address + "</td>" +
				"<td class=\"session-td\">" + element.session_id + "</td>" +
				"<td class=\"session-td\">" + element.user_agent + "</td>" +
				"<td class=\"session-td\">" + element.username + "</td>" +
				"<td class=\"session-td\">" + Math.round(element.validity) + "</td>" +
				"</tr>";
		});
		htmlStr += "</table>";
	}
	div = document.getElementById(htmlId);
	div.innerHTML = htmlStr;
	return htmlStr;
}


function printLockedProductsTable(data, htmlId) {
	if (data == undefined) {
		htmlStr = "<p>No locked Products found.</p>";
	}
	else if (Object.keys(data).length === 0) {
		htmlStr = "<p>No locked Products found.</p>";
	} else {
		htmlStr = "<table class=\"locked-products-table\" id=\"session-table\">" +
			"<tr>" +
			"<th class='locked-products-th'>Product</th>" +
			"<th class='locked-products-th'>Depots</th>"
		"</tr>";
		for (var key in data) {
			htmlStr += "<tr>" +
				"<td class=\"locked-products-td\" class=\"cell-breakWord \">" + key + "</td>" +
				"<td class=\"locked-products-td\">"
			data[key].forEach(element => {
				htmlStr += element + "<br>"
			});
			htmlStr += "</td>"
			htmlStr += "<td class=\"locked-products-td\"><input type=\"button\" onclick=\"unlockProduct('" + key + "')\" value=\"Unlock\"</td>"
			htmlStr += "</tr>";
		}
		htmlStr += "</table>";
	}
	div = document.getElementById(htmlId);
	div.innerHTML = htmlStr;
	return htmlStr;
}


function printAddonTable(data, htmlId) {
	if (data == undefined) {
		htmlStr = "<p>No addons loaded.</p>";
	}
	else if (data.length == 0) {
		htmlStr = "<p>No addons loaded.</p>";
	} else {
		htmlStr = "<table class=\"addon-table\" id=\"addon-table\">" +
			"<tr>" +
			"<th class='addon-th'>Addon ID</th>" +
			"<th class='addon-th'>Name</th>" +
			"<th class='addon-th'>Version</th>" +
			"<th class='addon-th'>Install path</th>" +
			"</tr>";
		data.forEach(element => {
			htmlStr += "<tr>" +
				"<td class=\"addon-td\"><a href=\"" + element.path + "\" target=\"_blank\">" + element.id + "</a></td>" +
				"<td class=\"addon-td\">" + element.name + "</td>" +
				"<td class=\"addon-td\">" + element.version + "</td>" +
				"<td class=\"addon-td\">" + element.install_path + "</td>" +
				"</tr>";
		});
		htmlStr += "</table>";
	}
	div = document.getElementById(htmlId);
	div.innerHTML = htmlStr;
	return htmlStr;
}


function printClientTable(data, htmlId) {
	if (data == undefined) {
		data = []
	}
	if (data.length == 0) {
		htmlStr = "<p>No clients are blocked by the server.</p>";
	} else {
		htmlStr = "<table class=\"rpc-table\" id=\"blocked-clients-table\">" +
			"<tr>" +
			"<th class='rpc-th'>Client</th>" +
			"<th class='rpc-th'>Action</th>" +
			"</tr>";
		data.forEach(element => {
			htmlStr += "<tr>";
			htmlStr += "<td class=\"rpc-td\">" + element + "</td>";
			htmlStr += "<td class=\"rpc-td\"><p onclick='unblockClient(\"" + element +
				"\")' style=\"cursor: pointer;\">unblock</p></td>";
			htmlStr += "</tr>";

		});
		htmlStr += "</table>";
	}
	div = document.getElementById(htmlId);
	div.innerHTML = htmlStr;
	return htmlStr;
}


function printRPCTable(data, htmlId) {
	let htmlStr = "<table class=\"rpc-table\">";
	htmlStr += "<tr>";
	keys = Object.keys(data[0]);
	Object.keys(data[0]).forEach(element => {
		htmlStr += "<th class=\"rpc-th\" onclick=\"loadRPCTable('" + element + "', " + true + ")\" onmouseover=\"\" style=\"cursor: pointer;\">" + element + "</th>";
	});
	htmlStr += "</tr>";

	data.forEach((element, idx) => {
		htmlStr += "<tr>";
		if (element["error"]) {
			keys.forEach(key => {
				htmlStr += "<td class=\"rpc-error-td\">" + element[key] + "</td>";
			});
		} else {
			keys.forEach(key => {
				if (key == "date") {
					date = formateDate(new Date(element[key]))
					htmlStr += "<td class=\"rpc-td\">" + date + "</td>";
				}
				else if (key == "duration") {
					duration = element[key].toFixed(4)
					htmlStr += "<td class=\"rpc-td\">" + duration + "</td>";
				}
				else {
					htmlStr += "<td class=\"rpc-td\">" + element[key] + "</td>";
				}
			});
		}
		htmlStr += "</tr>";
	});

	htmlStr += "</table>";
	div = document.getElementById(htmlId);
	div.innerHTML = htmlStr;
	return htmlStr;
}


var desc = true;
function sortRPCTable(data, sortKey) {
	data = result.sort((a, b) => {
		if (sortKey == "method") {
			var nameA = a[sortKey].toUpperCase();
			var nameB = b[sortKey].toUpperCase();
			if (nameA < nameB) {
				return -1;
			}
			if (nameA > nameB) {
				return 1;
			}
			return 0;
		} else if (sortKey == "date") {
			var dateA = new Date(a[sortKey])
			var dateB = new Date(b[sortKey])
			if (dateA < dateB) {
				return -1;
			}
			if (dateA > dateB) {
				return 1;
			}
			return 0;
		} else if (sortKey == "client") {
			var numA = Number(a[sortKey].split(".").map((num) => (`000${num}`).slice(-3)).join(""));
			var numB = Number(b[sortKey].split(".").map((num) => (`000${num}`).slice(-3)).join(""));
			if (numA < numB) {
				return -1;
			}
			if (numA > numB) {
				return 1;
			}
			return 0;
		} else {
			return Number(a[sortKey]) - Number(b[sortKey]);
		}
	});
	if (desc) {
		data = data.reverse();
		desc = false;
	} else {
		desc = true;
	}
	return data;

}


function createRequestJSON() {
	let apiJSON = {
		"id": 1,
		"jsonrpc": "2.0",
		"method": "",
		"params": []
	}

	let option = document.getElementById("method-select");
	let method = option.options[option.selectedIndex].text;
	let inputs = document.getElementById("tab-rpc-interface").getElementsByTagName("input");
	let parameter = [];

	apiJSON.method = method;

	document.getElementById("jsonrpc-request-error").innerHTML = "";
	for (i = 0; i < inputs.length; i++) {
		let name = null;
		let value = null;
		try {
			name = inputs[i].name.trim();
			value = inputs[i].value.trim();
			if (value) {
				parameter.push(JSON.parse(value));
			} else if (!name.startsWith("*")) {
				parameter.push(null);
			}
		} catch (e) {
			console.warn(`${name}: ${e}`);
			document.getElementById("jsonrpc-request-error").innerHTML = `${name}: ${e}`;
		}
	}

	apiJSON.params = parameter;
	return apiJSON;
}

function changeRequestJSON(name, value) {
	let apiJSON = createRequestJSON();
	outputToHTML(apiJSON, "jsonrpc-request");
}



function outputToHTML(json, id) {
	jsonStr = JSON.stringify(json, undefined, 2);
	jsonStr = syntaxHighlight(jsonStr);
	document.getElementById(id).style.visibility = 'visible'
	document.getElementById(id).innerHTML = jsonStr;
}

function decode(html) {
	var txt = document.createElement('textarea');
	txt.innerHTML = html;
	return txt.value;
}

function formateDate(date) {
	year = date.getFullYear();
	month = date.getMonth() + 1;
	dt = date.getDate();
	hour = date.getHours();
	minutes = date.getMinutes();
	seconds = date.getSeconds();

	if (dt < 10) {
		dt = '0' + dt;
	}
	if (month < 10) {
		month = '0' + month;
	}
	if (hour < 10) {
		hour = '0' + hour;
	}
	if (minutes < 10) {
		minutes = '0' + minutes;
	}
	if (seconds < 10) {
		seconds = '0' + seconds;
	}
	date = year + '-' + month + '-' + dt + ' ' + hour + ':' + minutes + ':' + seconds
	return date;
}


var terminal;
window.onresize = function () {
	if (terminal) terminal.fitAddon.fit();
};

function startTerminal() {
	terminal = new Terminal({
		cursorBlink: true,
		scrollback: 1000,
		fontSize: 14
	});

	const searchAddon = new SearchAddon.SearchAddon();
	terminal.loadAddon(searchAddon);
	const webLinksAddon = new WebLinksAddon.WebLinksAddon();
	terminal.loadAddon(webLinksAddon);
	terminal.fitAddon = new FitAddon.FitAddon();
	terminal.loadAddon(terminal.fitAddon);

	terminal.open(document.getElementById('terminal-xterm'));

	setTimeout(function () {
		document.getElementsByClassName('xterm-viewport')[0].setAttribute("style", "");

		terminal.fitAddon.fit();
		terminal.focus();

		console.log(`size: ${terminal.cols} cols, ${terminal.rows} rows`);

		let params = ["set_cookie_interval=30", `rows=${terminal.rows}`, `cols=${terminal.cols}`]
		let loc = window.location;
		let ws_uri;
		if (loc.protocol == "https:") {
			ws_uri = "wss:";
		} else {
			ws_uri = "ws:";
		}
		ws_uri += "//" + loc.host;
		terminal.websocket = new WebSocket(ws_uri + "/admin/terminal/ws?" + params.join('&'));
		terminal.websocket.binaryType = 'arraybuffer';
		terminal.websocket.onclose = function () {
			console.log("Terminal ws connection closed");
			terminal.writeln("\r\n\033[1;37m> Connection closed <\033[0m");
			terminal.write("\033[?25l"); // Make cursor invisible
		};
		terminal.websocket.onerror = function (error) {
			console.error(`Terminal ws connection error: ${JSON.stringify(error)}`);
			terminal.writeln("\r\n\033[1;31m> Connection error: " + JSON.stringify(error) + " <\033[0m");
			terminal.write("\033[?25l"); // Make cursor invisible
		};
		terminal.websocket.onmessage = function (event) {
			const message = msgpack.deserialize(event.data);
			//console.log(message);
			if (message.type == "terminal-read") {
				terminal.write(message.payload);
			}
			else if (message.type == "set-cookie") {
				console.log("Set-Cookie");
				document.cookie = message.payload;
			}
			else if (message.type == "file-transfer-result") {
				document.getElementsByClassName('xterm-selection-layer')[0].classList.remove("upload-active");
				if (message.payload.error) {
					const error = `File upload failed: ${JSON.stringify(message.payload)}`;
					console.error(error);
				}
				else {
					console.log(`File upload successful: ${JSON.stringify(message.payload)}`)
					const path = message.payload.result.path;
					terminal.websocket.send(msgpack.serialize({ "type": "terminal-write", "payload": path + "\033[D".repeat(path.length) }));
				}
			}
		}

		terminal.onData(function (data) {
			let message = msgpack.serialize({ "type": "terminal-write", "payload": data });
			terminal.websocket.send(message);
		})
		terminal.onResize(function (event) {
			//console.log("Resize:")
			//console.log(event);
			terminal.websocket.send(msgpack.serialize({ "type": "terminal-resize", "payload": { "rows": event.rows, "cols": event.cols } }));
		});

		const el = document.getElementsByClassName("xterm-screen")[0];
		el.ondragenter = function (event) {
			return false;
		};
		el.ondragover = function (event) {
			event.preventDefault();
		}
		el.ondragleave = function (event) {
			return false;
		};
		el.ondrop = function (event) {
			event.preventDefault();
			terminalFileUpload(event.dataTransfer.files[0]);
		}
	}, 100);
}


function toggleFullscreenTerminal() {
	if (!terminal) return;
	var elem = document.getElementById('terminal-xterm');
	if (elem.requestFullscreen) {
		elem.requestFullscreen();
	}
	terminal.fitAddon.fit();
}

function stopTerminal() {
	if (!terminal) return;
	terminal.dispose();
	terminal.websocket.close();
}

function changeTerminalFontSize(val) {
	if (!terminal) return;
	let size = terminal.getOption("fontSize");
	size += val;
	if (size < 1) { size = 1; }
	terminal.setOption("fontSize", size);
	terminal.fitAddon.fit();
}

function terminalFileUpload(file) {
	console.log("terminalFileUpload:")
	console.log(file);
	if (!terminal || !terminal.websocket) {
		console.error("No terminal connected")
		return;
	}

	let chunkSize = 100000;
	let fileId = createUUID();
	let chunk = 0;
	let offset = 0;

	var readChunk = function () {
		var reader = new FileReader();
		var blob = file.slice(offset, offset + chunkSize);
		reader.onload = function () {
			//console.log(offset);
			offset += chunkSize;
			chunk += 1;
			const more_data = (offset < file.size);
			let message = msgpack.serialize({
				"id": createUUID(),
				"type": "file-transfer",
				"payload": {
					"file_id": fileId,
					"chunk": chunk,
					"data": new Uint8Array(reader.result),
					"more_data": more_data
				}
			});
			document.getElementsByClassName('xterm-selection-layer')[0].classList.add("upload-active");
			terminal.websocket.send(message);

			if (more_data) {
				readChunk();
			}
		}
		reader.readAsArrayBuffer(blob);
	}

	document.getElementsByClassName('xterm-selection-layer')[0].classList.add("upload-active");
	let message = msgpack.serialize({
		"id": createUUID(),
		"type": "file-transfer",
		"payload": {
			"file_id": fileId,
			"chunk": chunk,
			"name": file.name,
			"size": file.size,
			"modified": file.lastModified,
			"data": null,
			"more_data": true
		}
	});
	document.getElementsByClassName('xterm-selection-layer')[0].classList.add("upload-active");
	terminal.websocket.send(message);
	readChunk();
}


function generateLiceningInfoTable(info, htmlId) {
	htmlStr = "<table id=\"licensing-info-table\">";
	for (const [key, val] of Object.entries(info)) {
		htmlStr += `<tr><td class="licensing-info-key">${key}</td><td>${val}</td></tr>`;
	}
	htmlStr += "</table>";
	div = document.getElementById(htmlId).innerHTML = htmlStr;
}

function generateLiceningDatesTable(dates, activeDate, htmlId) {
	htmlStr = "<table id=\"licensing-dates-table\"><tr><th>Module</th>";
	for (const date of Object.keys(Object.values(dates)[0])) {
		htmlStr += `<th>${date}</th>`;
	}
	htmlStr += "</tr>";
	for (const [moduleId, dateData] of Object.entries(dates)) {
		htmlStr += `<tr><td>${moduleId}</td>`;
		for (const [date, moduleData] of Object.entries(dateData)) {
			let title = "";
			for (const [k, v] of Object.entries(moduleData)) {
				title += `${k}: ${v}&#010;`;
			}
			const changed = moduleData['changed'] ? 'changed' : '';
			const active = date == activeDate ? 'active' : 'inactive';
			const text = moduleData['client_number'] == 999999999 ? 'unlimited' : moduleData['client_number'];
			htmlStr += `<td title="${title}" class="${changed} ${moduleData['state']} ${active}">${text}</td>`;
		}
		htmlStr += "</tr>";
	}
	htmlStr += "</table>";
	div = document.getElementById(htmlId).innerHTML = htmlStr;
}


function toggleTabMaximize() {
	tabcontent = document.getElementsByClassName("tabcontent");
	let buttonText = "Maximize";
	for (i = 0; i < tabcontent.length; i++) {
		if (tabcontent[i].style.display == "none") {
			continue;
		}
		if (tabcontent[i].classList.contains("maximize")) {
			tabcontent[i].classList.remove("maximize");
		}
		else {
			tabcontent[i].classList.add("maximize");
			buttonText = "Normal size";
		}
	}
	buttons = document.getElementsByClassName("tab-maximize");
	for (i = 0; i < buttons.length; i++) {
		buttons[i].innerHTML = buttonText;
	}
	if (terminal) terminal.fitAddon.fit();
}

document.onkeydown = function (evt) {
	evt = evt || window.event;
	if (evt.ctrlKey && evt.key == "F11") {
		toggleTabMaximize();
	}
};
