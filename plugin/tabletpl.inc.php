<?php
// PukiWiki tabletpl plugin: data-only reusable table templates.
// SPDX-License-Identifier: GPL-2.0-or-later
//
// Usage: #tabletpl(project_overview,title=Example,genre=SciFi,stage=Draft,owner=Team)
// Templates are reviewed JSON files in plugin/tabletpl_templates/.
// This plugin never modifies source pages or invokes arbitrary PHP from templates.

function plugin_tabletpl_convert()
{
    try {
        $wiki = plugin_tabletpl_compile(func_get_args());
        // Use PukiWiki's existing table parser; no replacement HTML renderer.
        return convert_html(explode("\n", $wiki));
    } catch (Exception $e) {
        return '<span class="tabletpl_error">' .
            htmlspecialchars('tabletpl: ' . $e->getMessage(), ENT_QUOTES, 'UTF-8') .
            '</span>';
    }
}

function plugin_tabletpl_compile($args)
{
    if (count($args) < 1 || count($args) > 17) {
        throw new InvalidArgumentException('Expected a template ID followed by named fields');
    }
    $id = trim($args[0]);
    if (!preg_match('/\A[a-z][a-z0-9_]{0,31}\z/D', $id)) {
        throw new InvalidArgumentException('Invalid template ID');
    }

    $folder = realpath(__DIR__ . '/tabletpl_templates');
    if ($folder === false) {
        throw new RuntimeException('Template catalog unavailable');
    }
    $candidate = $folder . DIRECTORY_SEPARATOR . $id . '.json';
    $filename = realpath($candidate);
    if ($filename === false || strpos($filename, $folder . DIRECTORY_SEPARATOR) !== 0 ||
        !is_file($filename) || filesize($filename) > 16384) {
        throw new InvalidArgumentException('Unknown or invalid template');
    }
    $definition = json_decode(file_get_contents($filename), true);
    if (!is_array($definition) || !isset($definition['id'], $definition['version'],
        $definition['fields'], $definition['rows'], $definition['columns']) ||
        $definition['id'] !== $id || !in_array($definition['version'], array(1, 2), true) ||
        !is_int($definition['columns']) || $definition['columns'] < 2 ||
        $definition['columns'] > 8 || !is_array($definition['fields']) ||
        count($definition['fields']) > 16 ||
        ($definition['version'] === 1 && (!is_array($definition['rows']) ||
        count($definition['rows']) < 1 || count($definition['rows']) > 32))) {
        throw new InvalidArgumentException('Invalid template schema');
    }
    if ($definition['version'] === 2) {
        if (count($args) !== 1) {
            throw new InvalidArgumentException('Dataset templates do not accept inline fields');
        }
        return plugin_tabletpl_compile_dataset($definition);
    }
    $fields = array();
    foreach ($definition['fields'] as $field) {
        if (!is_string($field) || !preg_match('/\A[a-z][a-z0-9_]{0,31}\z/D', $field) ||
            array_key_exists($field, $fields)) {
            throw new InvalidArgumentException('Invalid field schema');
        }
        $fields[$field] = null;
    }

    $given = array();
    foreach (array_slice($args, 1) as $pair) {
        if (!is_string($pair) || strpos($pair, '=') === false) {
            throw new InvalidArgumentException('Expected key=value argument');
        }
        $parts = explode('=', $pair, 2);
        $key = trim($parts[0]);
        $value = trim($parts[1]);
        if (!array_key_exists($key, $fields) || array_key_exists($key, $given)) {
            throw new InvalidArgumentException('Unknown or duplicate field');
        }
        if ($value === '' || strlen($value) > 240 ||
            !preg_match('/\A[\p{L}\p{M}\p{N}\p{Zs}.,!?:;\/_+\-（）【】《》，。！？：；、·%]+\z/uD', $value) ||
            preg_match('/\A(?:LEFT|RIGHT|CENTER|BOLD|COLOR|BGCOLOR|SIZE):/i', $value)) {
            throw new InvalidArgumentException('Invalid field value');
        }
        $given[$key] = $value;
    }
    if (count($given) !== count($fields)) {
        throw new InvalidArgumentException('One or more required fields missing');
    }

    $lines = array();
    $used = array();
    foreach ($definition['rows'] as $row) {
        if (!is_array($row) || !isset($row['kind'], $row['cells']) ||
            !in_array($row['kind'], array('header', 'body'), true) ||
            !is_array($row['cells']) || count($row['cells']) !== $definition['columns']) {
            throw new InvalidArgumentException('Invalid row schema');
        }
        $cells = array();
        foreach ($row['cells'] as $cell) {
            if (!is_string($cell) || strlen($cell) > 512) {
                throw new InvalidArgumentException('Invalid template cell');
            }
            // A cell may be a literal or exactly one named placeholder.
            if (preg_match('/\A\{\{([a-z][a-z0-9_]*)\}\}\z/D', $cell, $match)) {
                if (!array_key_exists($match[1], $given)) {
                    throw new InvalidArgumentException('Unknown template placeholder');
                }
                $used[$match[1]] = true;
                $cell = $given[$match[1]];
            } elseif (strpos($cell, '{{') !== false || strpos($cell, '}}') !== false) {
                throw new InvalidArgumentException('Invalid placeholder syntax');
            }
            // Reject template data that could become table structure or Wiki directives.
            if ($cell === '' || strlen($cell) > 512 ||
                !preg_match('/\A[\p{L}\p{M}\p{N}\p{Zs}.,!?:;\/_+\-（）【】《》，。！？：；、·%]+\z/uD', $cell) ||
                preg_match('/\A(?:LEFT|RIGHT|CENTER|BOLD|COLOR|BGCOLOR|SIZE):/i', $cell)) {
                throw new InvalidArgumentException('Unsafe template cell');
            }
            // PukiWiki requires '~' per cell for semantic TH tags;
            // row suffix 'h' alone only creates a THEAD section.
            $cells[] = ($row['kind'] === 'header' ? '~' : '') . $cell;
        }
        $lines[] = '|' . implode('|', $cells) . '|' . ($row['kind'] === 'header' ? 'h' : '');
    }
    if (count($used) !== count($fields)) {
        throw new InvalidArgumentException('Declared template fields are not used');
    }
    return implode("\n", $lines);
}


// v2: reviewed, variable-length JSON records with stable identifiers.
// Never accepts filesystem paths, arbitrary Wiki markup, or user-controlled HTML.
function plugin_tabletpl_compile_dataset($def)
{
    if (!isset($def['headers'], $def['records']) ||
        !is_array($def['headers']) || !is_array($def['records']) ||
        count($def['headers']) !== $def['columns'] ||
        count($def['fields']) !== $def['columns'] ||
        count($def['records']) < 1 || count($def['records']) > 100) {
        throw new InvalidArgumentException('Invalid dataset schema');
    }
    $keys = array();
    $types = array('text', 'number', 'date', 'enum', 'asset_ref');
    foreach ($def['fields'] as $field) {
        if (!is_array($field) || !isset($field['id'], $field['type']) ||
            !is_string($field['id']) || !preg_match('/\A[a-z][a-z0-9_]{0,31}\z/D', $field['id']) ||
            isset($keys[$field['id']]) || !in_array($field['type'], $types, true)) {
            throw new InvalidArgumentException('Invalid dataset field');
        }
        if ($field['type'] === 'enum' &&
            (!isset($field['options']) || !is_array($field['options']) ||
             count($field['options']) < 1 || count($field['options']) > 16)) {
            throw new InvalidArgumentException('Invalid enum options');
        }
        $keys[$field['id']] = $field;
    }
    $render = function ($value) {
        if (!is_string($value) || $value === '' || strlen($value) > 240 ||
            !preg_match('/\A[\p{L}\p{M}\p{N}\p{Zs}.,!?:;\/_+\-（）【】《》，。！？：；、·%]+\z/uD', $value) ||
            preg_match('/\A(?:LEFT|RIGHT|CENTER|BOLD|COLOR|BGCOLOR|SIZE):/i', $value)) {
            throw new InvalidArgumentException('Unsafe dataset text');
        }
        return $value;
    };
    $headers = array();
    foreach ($def['headers'] as $header) {
        $headers[] = '~' . $render($header);
    }
    $lines = array('|' . implode('|', $headers) . '|h');
    $seen = array();
    foreach ($def['records'] as $record) {
        if (!is_array($record) || !isset($record['row_id']) ||
            !is_string($record['row_id']) ||
            !preg_match('/\A[a-zA-Z][a-zA-Z0-9_-]{0,63}\z/D', $record['row_id']) ||
            isset($seen[$record['row_id']]) ||
            count($record) !== count($keys) + 1) {
            throw new InvalidArgumentException('Invalid or duplicate row ID');
        }
        $seen[$record['row_id']] = true;
        $cells = array();
        foreach ($keys as $name => $field) {
            if (!array_key_exists($name, $record)) {
                throw new InvalidArgumentException('Missing dataset field');
            }
            $v = $render($record[$name]);
            if ($field['type'] === 'number' && !preg_match('/\A[0-9]+(?:\.[0-9]+)?\z/D', $v)) {
                throw new InvalidArgumentException('Invalid number');
            }
            if ($field['type'] === 'date' &&
                (!preg_match('/\A\d{4}-\d{2}-\d{2}\z/D', $v) ||
                 !checkdate((int)substr($v, 5, 2), (int)substr($v, 8, 2), (int)substr($v, 0, 4)))) {
                throw new InvalidArgumentException('Invalid date');
            }
            if ($field['type'] === 'enum' && !in_array($v, $field['options'], true)) {
                throw new InvalidArgumentException('Invalid enum value');
            }
            if ($field['type'] === 'asset_ref' && !preg_match('/\Aasset_[a-f0-9]{64}\z/D', $v)) {
                throw new InvalidArgumentException('Invalid asset reference');
            }
            $cells[] = $v;
        }
        $lines[] = '|' . implode('|', $cells) . '|';
    }
    return implode("\n", $lines);
}
