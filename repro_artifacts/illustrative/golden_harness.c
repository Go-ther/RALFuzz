#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"

int main(void) {
    // Probe 1: Malformed/truncated input
    const char* bad_input = "{\"key\":";
    cJSON *json = cJSON_Parse(bad_input);
    if (json == NULL) {
        printf("Parse failed as expected for truncated input\n");
    } else {
        cJSON_Delete(json);
    }

    // Probe 2: Deep nesting (short but nested)
    const char* deep_input = "{\"a\":{\"b\":{\"c\":{\"d\":{}}}}}";
    json = cJSON_Parse(deep_input);
    if (json != NULL) {
        printf("Deep nesting parsed successfully\n");
        cJSON_Delete(json);
    }

    // Probe 3: Empty string boundary case
    json = cJSON_Parse("");
    if (json == NULL) {
        printf("Empty string correctly rejected\n");
    } else {
        cJSON_Delete(json);
    }

    return 0;
}
