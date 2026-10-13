<?php
// Standalone PHP compiler unit tests for PukiWiki tabletpl plugin.
// SPDX-License-Identifier: GPL-2.0-or-later
require dirname(__DIR__, 2) . '/plugin/tabletpl.inc.php';
function verify_tabletpl($ok, $message) {
    if (!$ok) { throw new RuntimeException('FAIL: ' . $message); }
    echo "PASS: " . $message . "\n";
}
function expect_tabletpl_rejection($args, $label) {
    try {
        plugin_tabletpl_compile($args);
    } catch (InvalidArgumentException $e) {
        echo "EXPECTED_REJECTION: " . $label . "\n";
        return;
    }
    throw new RuntimeException('No rejection: ' . $label);
}
$project = plugin_tabletpl_compile(array(
    'project_overview', 'title=欧罗巴黑晶纪',
    'genre=科幻电影', 'stage=筹备中', 'owner=周星宇'));
verify_tabletpl(strpos($project, '|~项目字段|~内容|h') === 0,
    'Native PukiWiki header cells with THEAD');
verify_tabletpl(strpos($project, '|项目名称|欧罗巴黑晶纪|') !== false,
    'Unicode project title is inserted as plain text');
verify_tabletpl(substr_count($project, "\n") === 4, 'Five table rows');
$shot = plugin_tabletpl_compile(array('shot_card','shot=SC017',
    'setting=欧罗巴基地', 'action=黑晶生长', 'duration=12秒'));
verify_tabletpl(strpos($shot, '|~镜头编号|~拍摄场景|~镜头内容|~时长|h') === 0,
    'Shot card has four header cells');
verify_tabletpl(strpos($shot, '|SC017|欧罗巴基地|黑晶生长|12秒|') !== false,
    'Shot card data present');
expect_tabletpl_rejection(array('../etc/passwd'), 'path traversal');
expect_tabletpl_rejection(array('unlisted'), 'unknown template');
expect_tabletpl_rejection(array('project_overview', 'title=A'), 'missing fields');
expect_tabletpl_rejection(array('project_overview', 'title=A', 'title=B',
    'genre=A', 'stage=B', 'owner=C'), 'duplicate field');
expect_tabletpl_rejection(array('project_overview', 'title=A',
    'genre=B', 'stage=C', 'owner=LEFT:forged'), 'Wiki directive');
expect_tabletpl_rejection(array('project_overview', 'title=A|#include',
    'genre=B', 'stage=C', 'owner=D'), 'Wiki delimiter/plugin injection');
expect_tabletpl_rejection(array('project_overview', 'title=<script>',
    'genre=B', 'stage=C', 'owner=D'), 'HTML markup injection');
expect_tabletpl_rejection(array('project_overview', 'title=A',
    'genre=B', 'stage=C', 'owner=D', 'unknown=x'), 'unknown key');
expect_tabletpl_rejection(array('shot_card', 'shot=A',
    'setting=B', 'action=C', 'duration='), 'empty value');
echo "TABLETPL_PHP_UNIT=PASS\n";
