; Kotlin declarations and imports used by the optional Tree-sitter extractor.
(class_declaration
  (type_identifier) @class.name) @class

(function_declaration
  (simple_identifier) @function.name) @function

(import_header
  (identifier) @import.path)
