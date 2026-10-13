<?php
// Regression coverage for the version 2 table dataset compiler.
require dirname(__DIR__, 2) . '/plugin/tabletpl.inc.php';
function check_v2($condition, $label) {
    if (!$condition) { throw new RuntimeException($label); }
    echo "PASS: $label\n";
}
$wiki = plugin_tabletpl_compile(array('shot_register_v2'));
check_v2(strpos($wiki, '|~镜头ID|~场次|~镜头内容|~时长秒|~状态|h') === 0, 'semantic header');
check_v2(strpos($wiki, '|SC02-001|SC02|巷道远景|6|已完成|') !== false, 'first row');
check_v2(strpos($wiki, '|SC02-002|SC02|人物跟拍|8.5|制作中|') !== false, 'second row');
check_v2(substr_count($wiki, "\n") === 2, 'variable data rows');
try {
    plugin_tabletpl_compile(array('shot_register_v2', 'foo=bar'));
    throw new RuntimeException('inline override not rejected');
} catch (InvalidArgumentException $e) {
    check_v2(true, 'reject inline override');
}
$def = json_decode(file_get_contents(dirname(__DIR__, 2) . '/plugin/tabletpl_templates/shot_register_v2.json'), true);
$def['records'][1]['row_id'] = $def['records'][0]['row_id'];
try {
    plugin_tabletpl_compile_dataset($def);
    throw new RuntimeException('duplicate row ID accepted');
} catch (InvalidArgumentException $e) {
    check_v2(true, 'reject duplicate row ID');
}
$def['records'][1]['row_id'] = 'sc02_sh002';
$def['records'][1]['action'] = '|#include(X)';
try {
    plugin_tabletpl_compile_dataset($def);
    throw new RuntimeException('unsafe table markup accepted');
} catch (InvalidArgumentException $e) {
    check_v2(true, 'reject wiki syntax injection');
}
echo "TABLETPL_V2_PHP_UNIT=PASS\n";
