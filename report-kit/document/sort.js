function rkSort(th){
  var table=th.closest('table'), tb=table.tBodies[0];
  var idx=Array.prototype.indexOf.call(th.parentNode.children, th);
  var asc=!(th.dataset.asc==='1'); th.dataset.asc=asc?'1':'0';
  var rows=Array.prototype.slice.call(tb.rows);
  rows.sort(function(a,b){
    var x=a.cells[idx].dataset.v, y=b.cells[idx].dataset.v;
    var nx=parseFloat(x), ny=parseFloat(y);
    var both=!isNaN(nx)&&!isNaN(ny);
    var c = both ? (nx-ny) : (x<y?-1:x>y?1:0);
    return asc?c:-c;
  });
  rows.forEach(function(r){ tb.appendChild(r); });
}
