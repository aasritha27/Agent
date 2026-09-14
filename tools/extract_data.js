var src = require("fs").readFileSync("/workspace/data/apps/university-room-scheduler/src/original.js", "utf8");
var app = src.slice(0, src.indexOf("Be(a(lt,{}),document"));
new Function(app + ";globalThis.__dump=JSON.stringify({sections:L,rooms:M,faculty:V,Ke:Ke,courses:Re,courseSections:ge,periods:O,days:ie});")();
var data = globalThis.__dump;
require("fs").writeFileSync("/workspace/github-agent/backend/seed/university_data.json", data);
var d = JSON.parse(data);
var lines = [
  "sections: " + d.sections.length,
  "rooms: " + d.rooms.length,
  "faculty: " + d.faculty.length,
  "courses: " + d.courses.length,
  "courseSections: " + d.courseSections.length,
  "periods: " + d.periods.length,
  "days: " + d.days.join(","),
  "sample section: " + JSON.stringify(d.sections[0]),
  "sample room: " + JSON.stringify(d.rooms[0]),
  "sample faculty: " + JSON.stringify(d.faculty[0]),
  "sample course: " + JSON.stringify(d.courses[0]),
  "sample courseSection: " + JSON.stringify(d.courseSections[0]),
  "sample period: " + JSON.stringify(d.periods[0])
];
require("fs").writeFileSync("/tmp/dump.txt", lines.join("\n"));
