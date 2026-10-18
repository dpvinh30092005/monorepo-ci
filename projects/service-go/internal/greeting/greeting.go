// Package greeting builds greeting messages.
package greeting

import (
	"errors"
	"strings"
)

// ErrEmptyName is returned when the name is blank.
var ErrEmptyName = errors.New("name must not be empty")

// Hello returns a greeting for name.
func Hello(name string) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return "", ErrEmptyName
	}
	return "Hello, " + name + "!", nil
}
