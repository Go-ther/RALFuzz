#include "cJSON.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void test_parse(const char *input) {
    cJSON *json = cJSON_Parse(input);
    if (json) {
        printf("PASS: parsed successfully\n");
        cJSON_Delete(json);
    } else {
        printf("FAIL: parse error\n");
    }
}

int main(void) {
    // Test 1: malformed truncated input
    test_parse("{\"key\": \"value");  // missing closing quote and brace

    // Test 2: deep nesting (3 levels)
    test_parse("{\"a\":{\"b\":{\"c\":1}}}");

    // Test 3: binary payload (null byte in middle)
    char binary_input[] = "{\"data\":\"ab\0cd\"}";
    test_parse(binary_input);

    // Test 4: numeric extreme (INT_MIN)
    test_parse("{\"val\":-2147483648}");

    // Test 5: empty string (boundary)
    test_parse("");

    // Test 6: NULL pointer (boundary)
    test_parse(NULL);

    return 0;
}
