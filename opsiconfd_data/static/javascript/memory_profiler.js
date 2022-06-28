function tracemallocSnapshot() {
	let limit = 25;
	document.getElementById("button-tracemalloc-snapshot").disabled = true;
	let req = doReq("GET", `/admin/memory/tracemalloc-snapshot-new?limit=${limit}`, handleError = false);
	req.then((result) => {
		result = request.responseText;
		result = JSON.parse(result);
		outputToHTML(result, "memory-values");
		document.getElementById("button-tracemalloc-snapshot").disabled = false;
		return result
	}).catch((error) => {
		console.warn(request.status, error.message);
		document.getElementById("button-tracemalloc-snapshot").disabled = false;
		return error.status;
	});
}

function objgraphSnapshot(update = false) {
	let max_obj_types = parseInt(document.getElementById("input-objgraph-max-obj-types").value);
	let max_obj = parseInt(document.getElementById("input-objgraph-max-obj").value);

	document.getElementById("button-objgraph-snapshot-new").disabled = true;
	document.getElementById("button-objgraph-snapshot-update").disabled = true;

	let url = `/admin/memory/objgraph-snapshot-new?max_obj_types=${max_obj_types}&max_obj=${max_obj}`;
	if (update) {
		url = "/admin/memory/objgraph-snapshot-update";
	}
	let req = doReq("GET", url, handleError = false);

	req.then((result) => {
		outputToHTML(result, "memory-values");
		document.getElementById("button-objgraph-snapshot-new").disabled = false;
		document.getElementById("button-objgraph-snapshot-update").disabled = false;
		return result
	}).catch((error) => {
		console.warn(error.status, error.message);
		document.getElementById("button-objgraph-snapshot-new").disabled = false;
		document.getElementById("button-objgraph-snapshot-update").disabled = false;
		return error.status;
	});
}

function objgraphShowBackrefs() {
	let obj_id = document.getElementById("input-objgraph-obj-id").value;
	let win = window.open("/admin/memory/objgraph-show-backrefs?obj_id=" + obj_id, '_blank');
	win.focus();
}

function loadMemoryInfo() {
	let req = doReq("GET", "/admin/memory-summary");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function takeMemorySnapshot() {
	document.getElementById("memory-info").style.visibility = 'visible';
	document.getElementById("memory-values").innerHTML = "loading...";
	let req = doReq("POST", "/admin/memory/snapshot");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function diffMemorySnapshots() {
	document.getElementById("memory-info").style.visibility = 'visible';
	document.getElementById("memory-values").innerHTML = "loading...";

	snapshotNumber1 = document.getElementById("snapshot1").value;
	snapshotNumber2 = document.getElementById("snapshot2").value;
	if (snapshotNumber1 == "") {
		snapshotNumber1 = 1
	}
	if (snapshotNumber2 == "") {
		snapshotNumber2 = -1
	}
	url = "/admin/memory/diff?snapshot1=" + snapshotNumber1 + "&snapshot2=" + snapshotNumber2
	let req = doReq("GET", url);
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function takeHeapSnapshot() {
	document.getElementById("memory-info").style.visibility = 'visible';
	document.getElementById("memory-values").innerHTML = "loading...";
	let req = doReq("POST", "/admin/memory/guppy");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function diffHeapSnapshots() {
	document.getElementById("memory-info").style.visibility = 'visible';
	document.getElementById("memory-values").innerHTML = "loading...";

	snapshotNumber1 = document.getElementById("snapshot1").value;
	snapshotNumber2 = document.getElementById("snapshot2").value;
	if (snapshotNumber1 == "") {
		snapshotNumber1 = 1
	}
	if (snapshotNumber2 == "") {
		snapshotNumber2 = -1
	}
	url = "/admin/memory/guppy/diff?snapshot1=" + snapshotNumber1 + "&snapshot2=" + snapshotNumber2
	let req = doReq("GET", url);
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function takeClassSnapshot() {
	document.getElementById("memory-info").style.visibility = 'visible';
	document.getElementById("memory-values").innerHTML = "loading...";
	className = document.getElementById("class-name").value;
	moduleName = document.getElementById("module-name").value;
	description = document.getElementById("description").value;
	body = {
		"module": moduleName,
		"class": className,
		"description": description
	}
	let req = doReq("POST", "/admin/memory/classtracker", body);
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function classSummary() {
	document.getElementById("memory-info").style.visibility = 'visible';
	document.getElementById("memory-values").innerHTML = "loading...";
	let req = doReq("GET", "/admin/memory/classtracker/summary");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function deleteMemorySnapshots() {
	let req = doReq("DELETE", "/admin/memory/snapshot");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function deleteHeapSnapshots() {
	let req = doReq("DELETE", "/admin/memory/guppy");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}

function deleteClassTracker() {
	let req = doReq("DELETE", "/admin/memory/classtracker");
	req.then((result) => {
		outputToHTML(result, "memory-values");
		return result
	});
}